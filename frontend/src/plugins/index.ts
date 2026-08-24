/**
 * Bundled frontend plugins.
 *
 * Importing this module for its side effects registers every in-tree plugin's
 * frontend half. Whether a registration is actually *used* is decided by the
 * backend: a section only renders when its Python package published a matching
 * descriptor at `/api/plugins`, so uninstalling `precursor-kanban` removes the
 * section even though its code is bundled.
 *
 * To add a plugin, drop a folder here whose entry file calls `registerSection`
 * (or `registerRenderer`) at module scope, then import it below.
 */

import "./kanban";
