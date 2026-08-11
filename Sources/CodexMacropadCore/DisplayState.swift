import Foundation

public enum TaskStatus: String, Decodable, Equatable, Sendable {
    case working
    case approval
    case done

    fileprivate var priority: Int {
        switch self {
        case .approval: 3
        case .done: 2
        case .working: 1
        }
    }

    fileprivate var label: String {
        rawValue.uppercased()
    }
}

public enum ActivityPhase: String, Decodable, Equatable, Sendable {
    case analyze
    case research
    case review
    case edit
    case verify
    case run

    fileprivate var label: String {
        rawValue.uppercased()
    }
}

public struct DisplaySnapshot: Equatable, Sendable {
    public let artist: String
    public let title: String
    public let status: TaskStatus?
    public let phase: ActivityPhase?
    public let activeCount: Int

    public var isActive: Bool {
        status != nil
    }

    public init(
        artist: String,
        title: String,
        status: TaskStatus?,
        phase: ActivityPhase? = nil,
        activeCount: Int
    ) {
        self.artist = artist
        self.title = title
        self.status = status
        self.phase = phase
        self.activeCount = activeCount
    }

    public static let inactive = DisplaySnapshot(
        artist: "",
        title: "",
        status: nil,
        phase: nil,
        activeCount: 0
    )
}

public enum DisplayStateReadResult: Equatable, Sendable {
    case valid(DisplaySnapshot)
    case invalid
}

public struct DisplayStateStore: Sendable {
    private static let entropyTextLimitBytes = 30

    private let stateURL: URL

    public init(stateURL: URL) {
        self.stateURL = stateURL
    }

    public func read(at date: Date = Date()) -> DisplayStateReadResult {
        guard
            let data = try? Data(contentsOf: stateURL),
            let envelope = try? JSONDecoder().decode(StateVersion.self, from: data)
        else {
            return .invalid
        }

        switch envelope.version {
        case 2:
            guard
                let state = try? JSONDecoder().decode(PersistedStateV2.self, from: data)
            else {
                return .invalid
            }
            return .valid(snapshotV2(state, at: date))
        case 3:
            guard
                let state = try? JSONDecoder().decode(PersistedStateV3.self, from: data)
            else {
                return .invalid
            }
            return .valid(snapshotV3(state, at: date))
        default:
            return .invalid
        }
    }

    public func snapshot(at date: Date = Date()) -> DisplaySnapshot {
        guard case .valid(let snapshot) = read(at: date) else {
            return .inactive
        }
        return snapshot
    }

    private func snapshotV2(
        _ state: PersistedStateV2,
        at date: Date
    ) -> DisplaySnapshot {
        let now = date.timeIntervalSince1970
        let activeSessions = state.sessions.values
            .filter { $0.expiresAt > now }
            .sorted { lhs, rhs in
                if lhs.status.priority != rhs.status.priority {
                    return lhs.status.priority > rhs.status.priority
                }
                return lhs.updatedAt > rhs.updatedAt
            }

        guard let selected = activeSessions.first else {
            return .inactive
        }

        let workspace = selected.workspace.isEmpty ? "workspace" : selected.workspace
        return DisplaySnapshot(
            artist: utf8Prefix(
                "CODEX · \(activeSessions.count) ACTIVE",
                maximumBytes: Self.entropyTextLimitBytes
            ),
            title: utf8Prefix(
                "\(selected.status.label): \(workspace)",
                maximumBytes: Self.entropyTextLimitBytes
            ),
            status: selected.status,
            phase: nil,
            activeCount: activeSessions.count
        )
    }

    private func snapshotV3(
        _ state: PersistedStateV3,
        at date: Date
    ) -> DisplaySnapshot {
        let now = date.timeIntervalSince1970
        let activeSessions = state.sessions.values
            .filter { $0.expiresAt > now }
            .sorted { lhs, rhs in
                if lhs.status.priority != rhs.status.priority {
                    return lhs.status.priority > rhs.status.priority
                }
                return lhs.updatedAt > rhs.updatedAt
            }

        guard let selected = activeSessions.first else {
            return .inactive
        }

        let workspace = selected.workspace.isEmpty ? "workspace" : selected.workspace
        let phase: ActivityPhase?
        let label: String
        switch selected.status {
        case .approval, .done:
            phase = nil
            label = selected.status.label
        case .working:
            let visible: ActivityPhase
            if let active = selected.activeTools.max(by: { lhs, rhs in
                lhs.value.startedAt == rhs.value.startedAt
                    ? lhs.key < rhs.key
                    : lhs.value.startedAt < rhs.value.startedAt
            }) {
                visible = active.value.phase
            } else if
                let recent = selected.recentPhase,
                let until = selected.recentPhaseUntil,
                until > now
            {
                visible = recent
            } else {
                visible = .analyze
            }
            phase = visible
            label = visible.label
        }

        let end = selected.status == .done ? selected.updatedAt : now
        let duration = formatTurnDuration(end - selected.turnStartedAt)
        return DisplaySnapshot(
            artist: utf8Prefix(
                "\(duration) · \(activeSessions.count) ACTIVE",
                maximumBytes: Self.entropyTextLimitBytes
            ),
            title: utf8Prefix(
                "\(label): \(workspace)",
                maximumBytes: Self.entropyTextLimitBytes
            ),
            status: selected.status,
            phase: phase,
            activeCount: activeSessions.count
        )
    }
}

private struct StateVersion: Decodable {
    let version: Int
}

private struct PersistedStateV2: Decodable {
    let sessions: [String: PersistedSessionV2]
}

private struct PersistedSessionV2: Decodable {
    let workspace: String
    let status: TaskStatus
    let updatedAt: TimeInterval
    let expiresAt: TimeInterval

    private enum CodingKeys: String, CodingKey {
        case workspace
        case status
        case updatedAt = "updated_at"
        case expiresAt = "expires_at"
    }
}

private struct PersistedStateV3: Decodable {
    let sessions: [String: PersistedSessionV3]
}

private struct PersistedSessionV3: Decodable {
    let workspace: String
    let status: TaskStatus
    let turnID: String?
    let turnStartedAt: TimeInterval
    let updatedAt: TimeInterval
    let expiresAt: TimeInterval
    let activeTools: [String: PersistedTool]
    let recentPhase: ActivityPhase?
    let recentPhaseUntil: TimeInterval?

    private enum CodingKeys: String, CodingKey {
        case workspace
        case status
        case turnID = "turn_id"
        case turnStartedAt = "turn_started_at"
        case updatedAt = "updated_at"
        case expiresAt = "expires_at"
        case activeTools = "active_tools"
        case recentPhase = "recent_phase"
        case recentPhaseUntil = "recent_phase_until"
    }
}

private struct PersistedTool: Decodable {
    let phase: ActivityPhase
    let startedAt: TimeInterval

    private enum CodingKeys: String, CodingKey {
        case phase
        case startedAt = "started_at"
    }
}

func formatTurnDuration(_ interval: TimeInterval) -> String {
    let seconds = max(0, Int(interval.rounded(.down)))
    if seconds < 3_600 {
        return String(format: "%02d:%02d", seconds / 60, seconds % 60)
    }
    return String(format: "%d:%02d", seconds / 3_600, (seconds % 3_600) / 60)
}

private func utf8Prefix(_ value: String, maximumBytes: Int) -> String {
    var result = ""
    for character in value {
        let candidate = result + String(character)
        guard candidate.utf8.count <= maximumBytes else {
            break
        }
        result = candidate
    }
    return result
}
