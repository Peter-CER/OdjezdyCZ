import os

private let logger = Logger(subsystem: "io.github.rp20048.Odjezdy", category: "app")

/// Diagnostic log line, readable with:  log stream --predicate 'subsystem == "io.github.rp20048.Odjezdy"'
func ovLog(_ message: String) {
    logger.notice("\(message, privacy: .public)")
}
