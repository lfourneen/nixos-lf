"""Automatic chooser backend; the real desktop/document portals mediate grants."""
import json
from pathlib import Path
from gi.repository import Gio, GLib

XML = """<node><interface name='org.freedesktop.impl.portal.FileChooser'>
<method name='OpenFile'><arg type='o' direction='in'/><arg type='s' direction='in'/>
<arg type='s' direction='in'/><arg type='s' direction='in'/><arg type='a{sv}' direction='in'/>
<arg type='u' direction='out'/><arg type='a{sv}' direction='out'/></method>
<property name='version' type='u' access='read'/></interface></node>"""


def method(connection, sender, path, interface, name, parameters, invocation):
    values = parameters.unpack()
    Path("/home/lfour/portal-request.json").write_text(json.dumps(values))
    result = {"uris": GLib.Variant("as", ["file:///home/lfour/private/selected.txt"])}
    invocation.return_value(GLib.Variant("(ua{sv})", (0, result)))


connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
connection.register_object("/org/freedesktop/portal/desktop",
    Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], method,
    lambda *args: GLib.Variant("u", 4), None)
Gio.bus_own_name_on_connection(connection, "org.freedesktop.impl.portal.desktop.test",
                             Gio.BusNameOwnerFlags.NONE, None, None)
GLib.MainLoop().run()
