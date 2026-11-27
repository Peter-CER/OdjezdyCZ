// The drop-down departure board. It only draws what the Python app describes over D-Bus
// (see odjezdy/view.py) and sends back the user's actions.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Pango from 'gi://Pango';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';

const BUS_NAME = 'io.github.rp20048.Odjezdy';
const OBJECT_PATH = '/io/github/rp20048/Odjezdy/Board';
const INTERFACE = 'io.github.rp20048.Odjezdy.Board';

function label(text, styleClass = '', {wrap = false, expand = false} = {}) {
    const w = new St.Label({
        text, style_class: styleClass, x_expand: expand, y_align: Clutter.ActorAlign.CENTER,
    });
    if (wrap) {
        w.clutter_text.line_wrap = true;
        w.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
        w.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
    }
    return w;
}

function box(children, {vertical = false, styleClass = '', expand = false} = {}) {
    const b = new St.BoxLayout({vertical, style_class: styleClass, x_expand: expand});
    for (const child of children) {
        if (child)
            b.add_child(child);
    }
    return b;
}

function button(content, onClick, styleClass = 'odjezdy-button', {expand = false} = {}) {
    const b = new St.Button({
        style_class: styleClass, can_focus: true, x_expand: expand,
        button_mask: St.ButtonMask.ONE | St.ButtonMask.THREE,
    });
    if (typeof content === 'string')
        b.label = content;
    else
        b.set_child(content);
    b.connect('clicked', (_b, mouseButton) => onClick(mouseButton));
    return b;
}

const Indicator = GObject.registerClass(
class OdjezdyIndicator extends PanelMenu.Button {
    _init(extensionPath) {
        super._init(0.5, 'Odjezdy');
        this.add_child(new St.Icon({
            gicon: Gio.icon_new_for_string(`${extensionPath}/odjezdy-symbolic.svg`),
            style_class: 'system-status-icon',
        }));

        this._cancellable = new Gio.Cancellable();
        this._timer = 0;
        this._drawn = {};
        this._settingsOpen = false;
        this._keyOpen = false;
        this._expandedRow = null;    // row whose right-click actions are showing

        // The board follows the desktop's light / dark style.
        this._interface = new Gio.Settings({schema_id: 'org.gnome.desktop.interface'});
        this._schemeChanged = this._interface.connect('changed::color-scheme',
            () => this._view && this._render());

        this._body = new St.BoxLayout({vertical: true, style_class: 'odjezdy-body'});
        this._search = this._makeSearch();
        this._results = new St.BoxLayout({vertical: true});
        this._footer = new St.BoxLayout({vertical: true});
        const scroll = new St.ScrollView({
            style_class: 'odjezdy-scroll',
            hscrollbar_policy: St.PolicyType.NEVER,
            child: box([this._body, this._search, this._results], {vertical: true}),
        });
        const root = box([scroll, this._footer], {vertical: true, styleClass: 'odjezdy-root'});
        this.menu.box.add_child(root);

        this.menu.connect('open-state-changed', (_m, open) => this._setOpen(open));

        this._signal = Gio.DBus.session.signal_subscribe(
            BUS_NAME, INTERFACE, 'Changed', OBJECT_PATH, null, Gio.DBusSignalFlags.NONE,
            () => this.menu.isOpen && this._refresh());
        // Whenever the app (re)appears, tell it we are its front end.
        this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, BUS_NAME,
            Gio.BusNameWatcherFlags.NONE, () => {
                this._call('Attach');
                if (this.menu.isOpen)
                    this._setOpen(true);
            }, null);
    }

    // --- D-Bus ---

    _call(method, params = null, onReply = null) {
        // Calling an app that isn't running starts it (D-Bus activation).
        Gio.DBus.session.call(BUS_NAME, OBJECT_PATH, INTERFACE, method, params, null,
            Gio.DBusCallFlags.NONE, 15000, this._cancellable, (connection, result) => {
                try {
                    const reply = connection.call_finish(result);
                    onReply?.(reply.deepUnpack());
                } catch (e) {
                    if (e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        return;
                    console.warn(`Odjezdy: ${method} failed: ${e.message}`);
                    if (method === 'GetView')
                        this._showUnavailable();
                }
            });
    }

    _action(name, arg = null) {
        this._call('Action', new GLib.Variant('(ss)', [name, JSON.stringify(arg)]));
    }

    _setOpen(open) {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = 0;
        }
        if (!open) {
            this._settingsOpen = this._keyOpen = false;
            this._expandedRow = null;
            this._search.hide();
            this._call('SetOpen', new GLib.Variant('(b)', [false]));
            return;
        }
        this._call('SetOpen', new GLib.Variant('(b)', [true]));
        this._refresh();
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
            this._refresh();    // countdowns
            return GLib.SOURCE_CONTINUE;
        });
    }

    _refresh() {
        this._call('GetView', null, ([json]) => {
            this._view = JSON.parse(json);
            this._render();
        });
    }

    // --- Drawing ---

    // Rebuild a part only when what it shows changed, so hover and clicks survive.
    _draw(part, container, data, build) {
        const key = JSON.stringify(data);
        if (this._drawn[part] === key)
            return;
        this._drawn[part] = key;
        container.destroy_all_children();
        for (const child of build()) {
            if (child)
                container.add_child(child);
        }
    }

    _showUnavailable() {
        this._drawn = {};
        this._search.hide();
        this._results.destroy_all_children();
        this._footer.destroy_all_children();
        this._body.destroy_all_children();
        this._body.add_child(label('The Odjezdy app is not responding.', 'odjezdy-title'));
        this._body.add_child(label('Is it installed? See linux/README.md.', 'odjezdy-secondary'));
    }

    _render() {
        const v = this._view;
        const searching = v.search !== null && !this._keyOpen;
        const boardClass = this._boardClass(v);
        this._draw('body', this._body, [v.stop, v.message, boardClass, this._keyOpen,
            searching, this._expandedRow], () => {
            if (this._keyOpen)
                return this._keyEntry(v.settings);
            if (v.stop)
                return this._stop(v.stop, boardClass);
            if (v.message)
                return this._message(v.message);
            return [];
        });
        this._body.visible = !searching;

        if (searching && !this._search.visible) {
            this._entry.set_text('');
            this._search.show();
            this._entry.grab_key_focus();
        }
        this._search.visible = searching;
        if (searching)
            this._entry.hint_text = v.search.hint;
        this._draw('results', this._results, searching ? v.search : null,
            () => searching ? this._searchResults(v.search) : []);

        this._draw('footer', this._footer, [v.footer, v.settings, this._settingsOpen],
            () => this._footerContent(v));
    }

    // Same rules as board_style() in view.py; the desktop style is only known here.
    _boardClass(v) {
        const systemDark = this._interface.get_string('color-scheme') === 'prefer-dark';
        let style = 'odjezdy-board';
        if (v.led)
            style += ' odjezdy-led';
        else if (v.boardColor === 'white')
            style += ' odjezdy-light odjezdy-white';
        else if (v.boardColor === 'grey' || (v.boardColor === 'auto' && !systemDark))
            style += ' odjezdy-light';
        return `${style} odjezdy-size-${v.textSize}`;
    }

    _message(m) {
        const buttons = m.buttons.map(([action, text]) =>
            button(text, () => this._action(action)));
        const row = box(buttons, {styleClass: 'odjezdy-row-spaced'});
        row.x_align = Clutter.ActorAlign.CENTER;
        return [
            label(m.title, 'odjezdy-title odjezdy-center', {wrap: true}),
            m.detail ? label(m.detail, 'odjezdy-secondary odjezdy-center', {wrap: true}) : null,
            row,
        ];
    }

    _stop(s, boardClass) {
        const header = box([
            box([label(s.name, 'odjezdy-title'), label(s.sub, 'odjezdy-secondary')],
                {vertical: true, expand: true}),
            label(s.icons),
            button('🔍', () => this._action('search'), 'odjezdy-flat'),
        ], {styleClass: 'odjezdy-row-spaced'});

        let tabs = null;
        if (s.searched) {
            tabs = box([
                label('Searched stop', '', {expand: true}),
                button('⌖ Back to nearby', () => this._action('nearby'), 'odjezdy-flat'),
            ], {styleClass: 'odjezdy-banner'});
        } else if (s.tabs.length) {
            tabs = box(s.tabs.map(t => button(t.label, () => this._action('select', t.id),
                t.active ? 'odjezdy-tab odjezdy-tab-active' : 'odjezdy-tab', {expand: true})),
            {styleClass: 'odjezdy-tabs'});
        }

        const notices = s.notices.length
            ? box(s.notices.map(n => label(`⚠ ${n}`, '', {wrap: true})),
                {vertical: true, styleClass: 'odjezdy-notice'})
            : null;
        const alert = s.alert
            ? box([
                label(s.alert, '', {expand: true}),
                button('Cancel', () => this._action('cancelAlert'), 'odjezdy-flat'),
            ], {styleClass: 'odjezdy-banner'})
            : null;

        const board = box([], {vertical: true, styleClass: boardClass});
        if (s.placeholder)
            board.add_child(label(s.placeholder, 'odjezdy-placeholder'));
        // One badge column for the whole board, wide enough for "RJ 1021", so that
        // destinations line up.
        const longest = Math.max(0, ...s.sections.flatMap(x => x.rows.map(d => d.line.length)));
        const column = Math.max(3.6, 0.6 * longest + 1.8).toFixed(1);
        for (const section of s.sections) {
            const head = box([
                label(section.title, 'odjezdy-section', {expand: true}),
                label(section.more, 'odjezdy-section'),
            ], {expand: true});
            board.add_child(section.more
                ? button(head, () => this._action('expand', section.id), 'odjezdy-line',
                    {expand: true})
                : box([head], {styleClass: 'odjezdy-line', expand: true}));
            for (const d of section.rows) {
                board.add_child(this._departure(d, column));
                if (this._expandedRow === d.id)
                    board.add_child(this._departureActions(d));
            }
        }
        return [header, tabs, notices, alert, board];
    }

    _departure(d, column) {
        // A bin around the label: centres "9" and "B", and never cuts "RJ 1021" to "RJ 1…".
        const text = new St.Label({
            text: d.line, x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER,
        });
        text.clutter_text.ellipsize = Pango.EllipsizeMode.NONE;
        const badge = new St.Bin({
            style_class: 'odjezdy-badge', child: text, y_align: Clutter.ActorAlign.CENTER,
        });
        badge.set_style(`background-color: ${d.bg}; color: ${d.fg};`);
        const holder = box([badge]);
        holder.set_style(`width: ${column}em;`);

        const destination = label(d.destination,
            d.cancelled ? 'odjezdy-destination odjezdy-faded' : 'odjezdy-destination',
            {expand: true});
        if (d.cancelled) {
            const strike = new Pango.AttrList();
            strike.insert(Pango.attr_strikethrough_new(true));
            destination.clutter_text.set_attributes(strike);
        }

        let timeClass = 'odjezdy-time';
        if (d.cancelled)
            timeClass += ' odjezdy-red';
        else if (d.now)
            timeClass += ' odjezdy-now';
        const top = box([
            d.alert ? label('🔔', 'odjezdy-small') : null,
            d.live ? label('●', 'odjezdy-dot') : null,
            label(d.time, timeClass),
        ], {styleClass: 'odjezdy-times'});
        top.x_align = Clutter.ActorAlign.END;
        let delay = null;
        if (d.planned) {
            const planned = label(d.planned, 'odjezdy-small odjezdy-faded');
            const strike = new Pango.AttrList();
            strike.insert(Pango.attr_strikethrough_new(true));
            planned.clutter_text.set_attributes(strike);
            delay = box([planned, label(d.expected, 'odjezdy-small odjezdy-late')],
                {styleClass: 'odjezdy-times'});
            delay.x_align = Clutter.ActorAlign.END;
        }

        const content = box([
            holder,
            destination,
            box([top, delay], {vertical: true}),
        ], {expand: true});
        // Leaves before you can walk there: dimmed.
        if (d.dim)
            content.opacity = 100;

        // Left click: leave-now alert. Right click: more actions.
        return button(content, mouseButton => {
            if (mouseButton === Clutter.BUTTON_SECONDARY) {
                this._expandedRow = this._expandedRow === d.id ? null : d.id;
                this._render();
            } else if (!d.cancelled) {
                this._action('alert', d.id);
            }
        }, 'odjezdy-line', {expand: true});
    }

    _departureActions(d) {
        const run = (action, arg) => {
            this._expandedRow = null;
            this._action(action, arg);
        };
        return box([
            d.cancelled ? null : button(d.alert ? 'Cancel Alert' : 'Leave-Now Alert',
                () => run('alert', d.id), 'odjezdy-board-button'),
            button(d.favourite ? `Remove ${d.line} from Your Lines` : `Add ${d.line} to Your Lines`,
                () => run('favourite', d.line), 'odjezdy-board-button'),
        ], {styleClass: 'odjezdy-actions'});
    }

    _makeSearch() {
        this._entry = new St.Entry({
            style_class: 'odjezdy-entry', can_focus: true, x_expand: true,
        });
        this._entry.clutter_text.connect('text-changed',
            () => this._search.visible && this._action('searchText', this._entry.get_text()));
        this._entry.clutter_text.connect('activate', () => this._action('choose', 0));
        const row = box([this._entry, button('Cancel', () => this._action('closeSearch'))],
            {styleClass: 'odjezdy-row-spaced'});
        row.hide();
        return row;
    }

    _searchResults(s) {
        const rows = s.results.map((r, i) => button(box([
            box([label(r.name), label(r.sub, 'odjezdy-secondary')],
                {vertical: true, expand: true}),
            label(r.icons),
        ], {expand: true}), () => this._action('choose', i), 'odjezdy-result', {expand: true}));
        return [
            s.heading ? label(s.heading, 'odjezdy-secondary') : null,
            ...rows,
            s.empty ? label(s.empty, 'odjezdy-secondary odjezdy-center') : null,
        ];
    }

    _keyEntry(settings) {
        const entry = new St.PasswordEntry({
            style_class: 'odjezdy-entry', can_focus: true, x_expand: true,
            hint_text: settings.hasKey ? 'Paste a new key' : 'Paste key',
        });
        const close = () => {
            this._keyOpen = false;
            this._render();
        };
        const save = () => {
            if (entry.get_text().trim())
                this._action('key', entry.get_text());
            close();
        };
        entry.clutter_text.connect('activate', save);
        return [
            label('Golemio API key (Prague)', 'odjezdy-title'),
            label('Optional. Without it Prague uses MOTIS; with it you get second-accurate ' +
                'times, live buses and stop notices. Free at api.golemio.cz, stored only on ' +
                'this computer.', 'odjezdy-secondary', {wrap: true}),
            entry,
            box([
                button('Get a key', () => {
                    this.menu.close();
                    Gio.AppInfo.launch_default_for_uri(settings.signUpUrl, null);
                }),
                settings.hasKey ? button('Remove', () => {
                    this._action('key', '');
                    close();
                }) : null,
                button('Cancel', close),
                button('Save', save),
            ], {styleClass: 'odjezdy-row-spaced'}),
        ];
    }

    _footerContent(v) {
        const bar = box([
            label(v.footer, 'odjezdy-secondary', {expand: true}),
            button('🔍', () => {
                this._keyOpen = false;
                this._action('search');
            }, 'odjezdy-flat'),
            button('⟳', () => this._action('reload'), 'odjezdy-flat'),
            button('⚙', () => {
                this._settingsOpen = !this._settingsOpen;
                this._render();
            }, 'odjezdy-flat'),
        ], {styleClass: 'odjezdy-row-spaced'});
        if (!this._settingsOpen)
            return [bar];

        const s = v.settings;
        const item = (text, onClick) => button(label(text, '', {expand: true}), onClick,
            'odjezdy-result', {expand: true});
        const lead = box(s.leadChoices.map(m => button(`${m}`, () => this._action('lead', m),
            m === s.lead ? 'odjezdy-tab odjezdy-tab-active' : 'odjezdy-tab', {expand: true})),
        {styleClass: 'odjezdy-tabs'});
        const choices = (action, options, current) => box(options.map(([value, text]) =>
            button(text, () => this._action(action, value),
                value === current ? 'odjezdy-tab odjezdy-tab-active' : 'odjezdy-tab',
                {expand: true})), {styleClass: 'odjezdy-tabs'});
        return [bar, box([
            label('Board colour', 'odjezdy-secondary'),
            choices('boardColor', s.boardColors, s.boardColor),
            label('Text size', 'odjezdy-secondary'),
            choices('textSize', s.textSizes, s.textSize),
            item(`${s.led ? '✓ ' : ''}Sign-style Amber Text`, () => this._action('led', !s.led)),
            label('Leave-now alert: minutes before I must leave', 'odjezdy-secondary'),
            lead,
            s.setHome ? item(s.setHome, () => this._action('setHome')) : null,
            s.hasHome ? item('Forget Home Location', () => this._action('forgetHome')) : null,
            item('Golemio API Key (Prague, optional)…', () => {
                this._keyOpen = true;
                this._settingsOpen = false;
                this._action('closeSearch');
                this._render();
            }),
        ], {vertical: true, styleClass: 'odjezdy-settings'})];
    }

    destroy() {
        if (this._timer)
            GLib.source_remove(this._timer);
        this._timer = 0;
        Gio.DBus.session.signal_unsubscribe(this._signal);
        Gio.bus_unwatch_name(this._watch);
        this._interface.disconnect(this._schemeChanged);
        this._cancellable.cancel();
        // Hand back to the app's own tray icon, without starting an app that isn't running.
        Gio.DBus.session.call(BUS_NAME, OBJECT_PATH, INTERFACE, 'Detach', null, null,
            Gio.DBusCallFlags.NO_AUTO_START, -1, null, (connection, result) => {
                try {
                    connection.call_finish(result);
                } catch {
                    // not running: nothing to hand back
                }
            });
        super.destroy();
    }
});

export default class OdjezdyExtension extends Extension {
    enable() {
        this._indicator = new Indicator(this.path);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
