import SwiftUI
import AppKit
import UserNotifications

@main
struct OdjezdyApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var state = AppState.shared
    @AppStorage(AppState.showIconKey) private var showIcon = true

    var body: some Scene {
        MenuBarExtra(isInserted: $showIcon) {
            PopoverView(state: state)
        } label: {
            Image(nsImage: MenuBarIcon.image)
                .accessibilityLabel("Odjezdy")
        }
        .menuBarExtraStyle(.window)
    }
}

extension AppState {
    static let shared = AppState()
}

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    /// Show leave-now alerts as banners even while the popover is open (app in the foreground);
    /// without this macOS silently drops them.
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        Task { @MainActor in AppState.shared.launched() }
    }

    /// Launching the app again (Spotlight, Finder) activates it — that brings a hidden icon back.
    func applicationDidBecomeActive(_ notification: Notification) {
        Task { @MainActor in AppState.shared.reopened() }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        Task { @MainActor in AppState.shared.reopened() }
        return false
    }
}

/// The menu bar glyph. The stock tram symbol is taller than most status icons and top-heavy
/// (big body, thin rails), so it reads as sitting high. Draw it a bit smaller and nudge it down.
enum MenuBarIcon {
    static let pointSize: CGFloat = 13.5   // glyph size; stock rendering is ~15 pt
    static let nudgeDown: CGFloat = 0.5    // points, optical centring

    static let image: NSImage = {
        let config = NSImage.SymbolConfiguration(pointSize: pointSize, weight: .regular)
        guard let sym = NSImage(systemSymbolName: "tram.fill", accessibilityDescription: "Odjezdy")?
            .withSymbolConfiguration(config) else { return NSImage() }
        let canvas = NSSize(width: ceil(sym.size.width), height: 18)
        let img = NSImage(size: canvas, flipped: false) { rect in
            let y = (rect.height - sym.size.height) / 2 - nudgeDown
            sym.draw(in: NSRect(x: (rect.width - sym.size.width) / 2, y: y,
                                width: sym.size.width, height: sym.size.height))
            return true
        }
        img.isTemplate = true   // black in light menu bars, white in dark ones
        return img
    }()
}
