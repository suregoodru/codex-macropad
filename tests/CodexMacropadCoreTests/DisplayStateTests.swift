import Foundation
import XCTest
@testable import CodexMacropadCore

final class DisplayStateTests: XCTestCase {
    private var temporaryDirectory: URL!
    private var stateURL: URL!

    override func setUpWithError() throws {
        temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(
            at: temporaryDirectory,
            withIntermediateDirectories: true
        )
        stateURL = temporaryDirectory.appendingPathComponent("state.json")
    }

    override func tearDownWithError() throws {
        if let temporaryDirectory {
            try? FileManager.default.removeItem(at: temporaryDirectory)
        }
    }

    func testV2SelectsApprovalBeforeNewerDoneAndWorking() throws {
        try writeStateV2([
            sessionV2("working", workspace: "alpha", updatedAt: 30, expiresAt: 200),
            sessionV2("done", workspace: "beta", updatedAt: 20, expiresAt: 200),
            sessionV2("approval", workspace: "gamma", updatedAt: 10, expiresAt: 200),
        ])

        let snapshot = DisplayStateStore(stateURL: stateURL)
            .snapshot(at: Date(timeIntervalSince1970: 100))

        XCTAssertEqual(snapshot.title, "APPROVAL: gamma")
        XCTAssertEqual(snapshot.artist, "CODEX · 3 ACTIVE")
        XCTAssertEqual(snapshot.status, .approval)
        XCTAssertEqual(snapshot.activeCount, 3)
        XCTAssertTrue(snapshot.isActive)
    }

    func testV2UsesNewestSessionWhenPrioritiesMatch() throws {
        try writeStateV2([
            sessionV2("working", workspace: "older", updatedAt: 10, expiresAt: 200),
            sessionV2("working", workspace: "newer", updatedAt: 20, expiresAt: 200),
        ])

        let snapshot = DisplayStateStore(stateURL: stateURL)
            .snapshot(at: Date(timeIntervalSince1970: 100))

        XCTAssertEqual(snapshot.title, "WORKING: newer")
        XCTAssertEqual(snapshot.status, .working)
    }

    func testV2ExcludesExpiredSessionsFromSelectionAndCount() throws {
        try writeStateV2([
            sessionV2("approval", workspace: "expired", updatedAt: 90, expiresAt: 100),
            sessionV2("done", workspace: "fresh", updatedAt: 80, expiresAt: 101),
        ])

        let snapshot = DisplayStateStore(stateURL: stateURL)
            .snapshot(at: Date(timeIntervalSince1970: 100))

        XCTAssertEqual(snapshot.title, "DONE: fresh")
        XCTAssertEqual(snapshot.artist, "CODEX · 1 ACTIVE")
        XCTAssertEqual(snapshot.activeCount, 1)
    }

    func testV2BuildsTitleForEveryStatus() throws {
        for (status, expected) in [
            ("working", "WORKING: project"),
            ("approval", "APPROVAL: project"),
            ("done", "DONE: project"),
        ] {
            try writeStateV2([
                sessionV2(status, workspace: "project", updatedAt: 1, expiresAt: 200)
            ])

            let snapshot = DisplayStateStore(stateURL: stateURL)
                .snapshot(at: Date(timeIntervalSince1970: 100))

            XCTAssertEqual(snapshot.title, expected)
        }
    }

    func testV2TruncatesTitleWithoutSplittingUTF8() throws {
        try writeStateV2([
            sessionV2(
                "approval",
                workspace: "очень-длинное-название-проекта",
                updatedAt: 1,
                expiresAt: 200
            )
        ])

        let title = DisplayStateStore(stateURL: stateURL)
            .snapshot(at: Date(timeIntervalSince1970: 100)).title

        XCTAssertLessThanOrEqual(title.utf8.count, 30)
        XCTAssertNotNil(title.data(using: .utf8))
        XCTAssertTrue(title.hasPrefix("APPROVAL: "))
    }

    func testV2KeepsLegacyWorkingPresentation() throws {
        try writeStateV2([
            sessionV2("working", workspace: "project", updatedAt: 100, expiresAt: 400)
        ])

        let snapshot = DisplayStateStore(stateURL: stateURL)
            .snapshot(at: Date(timeIntervalSince1970: 200))

        XCTAssertEqual(snapshot.artist, "CODEX · 1 ACTIVE")
        XCTAssertEqual(snapshot.title, "WORKING: project")
        XCTAssertNil(snapshot.phase)
    }

    func testV3SelectsNewestActiveToolPhase() throws {
        try writeStateV3([
            "s1": sessionV3(
                "working",
                activeTools: [
                    "older": tool("research", startedAt: 105),
                    "newer": tool("verify", startedAt: 106),
                ]
            )
        ])

        let snapshot = DisplayStateStore(stateURL: stateURL)
            .snapshot(at: Date(timeIntervalSince1970: 130))

        XCTAssertEqual(snapshot.title, "VERIFY: macropad")
        XCTAssertEqual(snapshot.artist, "00:30 · 1 ACTIVE")
        XCTAssertEqual(snapshot.phase, .verify)
        XCTAssertEqual(snapshot.status, .working)
    }

    func testV3ApprovalAndDoneOverrideWorkingPhase() throws {
        for (status, expected) in [("approval", "APPROVAL"), ("done", "DONE")] {
            try writeStateV3([
                "s1": sessionV3(
                    status,
                    activeTools: ["tool": tool("edit", startedAt: 105)]
                )
            ])

            let snapshot = DisplayStateStore(stateURL: stateURL)
                .snapshot(at: Date(timeIntervalSince1970: 130))

            XCTAssertEqual(snapshot.title, "\(expected): macropad")
            XCTAssertNil(snapshot.phase)
        }
    }

    func testV3SelectsApprovalSessionBeforeNewerWorkingSession() throws {
        try writeStateV3([
            "working": sessionV3("working", workspace: "newer", updatedAt: 120),
            "approval": sessionV3("approval", workspace: "waiting", updatedAt: 110),
        ])

        let snapshot = DisplayStateStore(stateURL: stateURL)
            .snapshot(at: Date(timeIntervalSince1970: 130))

        XCTAssertEqual(snapshot.title, "APPROVAL: waiting")
        XCTAssertEqual(snapshot.artist, "00:30 · 2 ACTIVE")
        XCTAssertEqual(snapshot.activeCount, 2)
    }

    func testV3BreaksEqualToolTimestampsByIdentifier() throws {
        try writeStateV3([
            "s1": sessionV3(
                "working",
                activeTools: [
                    "a-tool": tool("research", startedAt: 105),
                    "z-tool": tool("edit", startedAt: 105),
                ]
            )
        ])

        let snapshot = DisplayStateStore(stateURL: stateURL)
            .snapshot(at: Date(timeIntervalSince1970: 130))

        XCTAssertEqual(snapshot.phase, .edit)
        XCTAssertEqual(snapshot.title, "EDIT: macropad")
    }

    func testV3UsesRecentPhaseUntilDeadlineThenAnalyze() throws {
        try writeStateV3([
            "s1": sessionV3(
                "working",
                activeTools: [:],
                recentPhase: "research",
                recentPhaseUntil: 120
            )
        ])
        let store = DisplayStateStore(stateURL: stateURL)

        XCTAssertEqual(
            store.snapshot(at: Date(timeIntervalSince1970: 119.999)).title,
            "RESEARCH: macropad"
        )
        XCTAssertEqual(
            store.snapshot(at: Date(timeIntervalSince1970: 120)).title,
            "ANALYZE: macropad"
        )
    }

    func testFormatsTurnDurationAcrossOneHourBoundary() {
        XCTAssertEqual(formatTurnDuration(0), "00:00")
        XCTAssertEqual(formatTurnDuration(154), "02:34")
        XCTAssertEqual(formatTurnDuration(3_599), "59:59")
        XCTAssertEqual(formatTurnDuration(3_600), "1:00")
        XCTAssertEqual(formatTurnDuration(7_439), "2:03")
    }

    func testDoneFreezesTimerAtUpdatedAt() throws {
        try writeStateV3([
            "s1": sessionV3(
                "done",
                turnStartedAt: 100,
                updatedAt: 254,
                expiresAt: 400
            )
        ])

        let snapshot = DisplayStateStore(stateURL: stateURL)
            .snapshot(at: Date(timeIntervalSince1970: 300))

        XCTAssertEqual(snapshot.artist, "02:34 · 1 ACTIVE")
        XCTAssertEqual(snapshot.title, "DONE: macropad")
    }

    func testReturnsInactiveForEmptySessions() throws {
        try writeStateV3([:])

        XCTAssertEqual(
            DisplayStateStore(stateURL: stateURL)
                .snapshot(at: Date(timeIntervalSince1970: 100)),
            .inactive
        )
    }

    func testReturnsInactiveForMissingMalformedAndUnknownVersion() throws {
        let store = DisplayStateStore(stateURL: stateURL)
        XCTAssertEqual(store.snapshot(at: Date(timeIntervalSince1970: 100)), .inactive)

        try "not-json".write(to: stateURL, atomically: true, encoding: .utf8)
        XCTAssertEqual(store.snapshot(at: Date(timeIntervalSince1970: 100)), .inactive)

        try #"{"version":99,"sessions":{}}"#
            .write(to: stateURL, atomically: true, encoding: .utf8)
        XCTAssertEqual(store.snapshot(at: Date(timeIntervalSince1970: 100)), .inactive)
    }

    func testReturnsInactiveWhenV3SessionHasUnknownPhase() throws {
        try writeStateV3([
            "bad": sessionV3(
                "working",
                activeTools: ["tool": tool("unknown", startedAt: 1)]
            )
        ])

        XCTAssertEqual(
            DisplayStateStore(stateURL: stateURL)
                .snapshot(at: Date(timeIntervalSince1970: 100)),
            .inactive
        )
    }

    private func tool(_ phase: String, startedAt: Double) -> [String: Any] {
        ["phase": phase, "started_at": startedAt]
    }

    private func sessionV3(
        _ status: String,
        workspace: String = "macropad",
        turnStartedAt: Double = 100,
        updatedAt: Double = 110,
        expiresAt: Double = 1_000,
        activeTools: [String: Any] = [:],
        recentPhase: String? = nil,
        recentPhaseUntil: Double? = nil
    ) -> [String: Any] {
        [
            "workspace": workspace,
            "status": status,
            "turn_id": "turn-1",
            "turn_started_at": turnStartedAt,
            "updated_at": updatedAt,
            "expires_at": expiresAt,
            "active_tools": activeTools,
            "recent_phase": recentPhase.map { $0 as Any } ?? NSNull(),
            "recent_phase_until": recentPhaseUntil.map { $0 as Any } ?? NSNull(),
        ]
    }

    private func writeStateV3(_ sessions: [String: Any]) throws {
        let data = try JSONSerialization.data(
            withJSONObject: ["version": 3, "sessions": sessions],
            options: [.sortedKeys]
        )
        try data.write(to: stateURL)
    }

    private func sessionV2(
        _ status: String,
        workspace: String,
        updatedAt: Double,
        expiresAt: Double
    ) -> [String: Any] {
        [
            "workspace": workspace,
            "status": status,
            "updated_at": updatedAt,
            "expires_at": expiresAt,
        ]
    }

    private func writeStateV2(_ sessions: [[String: Any]]) throws {
        var keyed: [String: Any] = [:]
        for (index, session) in sessions.enumerated() {
            keyed["s\(index)"] = session
        }
        let data = try JSONSerialization.data(
            withJSONObject: ["version": 2, "sessions": keyed],
            options: [.sortedKeys]
        )
        try data.write(to: stateURL)
    }
}
