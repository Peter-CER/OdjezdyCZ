import Foundation
import ServiceManagement

/// Start-at-login via SMAppService (macOS 13+). Enabled automatically on first launch.
enum LoginItem {
    static var isEnabled: Bool { SMAppService.mainApp.status == .enabled }

    static func set(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            ovLog("login item change failed: \(error.localizedDescription)")
        }
    }

    static func enableOnFirstLaunch() {
        // Only for the installed copy, not for builds run from Xcode / build folders.
        // Keyed by path, so a renamed/moved app registers itself again.
        let key = "loginItemSetUpForPath"
        let path = Bundle.main.bundlePath
        guard path.hasPrefix("/Applications/"),
              UserDefaults.standard.string(forKey: key) != path else { return }
        UserDefaults.standard.set(path, forKey: key)
        set(true)
    }
}
