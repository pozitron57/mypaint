# This file is part of MyPaint.
# Copyright (C) 2026 by the MyPaint Development Team.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

"""Helpers for turning GDK key events into MyPaint accelerators."""

import sys
import unicodedata

from lib.gibindings import Gdk
from lib.gibindings import Gtk


# macOS virtual keycodes identify physical positions.  GTK's Quartz backend
# only exposes the currently selected input source in its keymap, so there is
# no Latin group to fall back to when that source is Cyrillic, Greek, etc.
# Keep an ANSI-US level 0/1 map for shortcut lookup only.  The translated
# active-layout accelerator is always returned first.
_MACOS_ANSI_KEY_LEVELS = {
    0x00: ("a", "A"),
    0x01: ("s", "S"),
    0x02: ("d", "D"),
    0x03: ("f", "F"),
    0x04: ("h", "H"),
    0x05: ("g", "G"),
    0x06: ("z", "Z"),
    0x07: ("x", "X"),
    0x08: ("c", "C"),
    0x09: ("v", "V"),
    0x0B: ("b", "B"),
    0x0C: ("q", "Q"),
    0x0D: ("w", "W"),
    0x0E: ("e", "E"),
    0x0F: ("r", "R"),
    0x10: ("y", "Y"),
    0x11: ("t", "T"),
    0x12: ("1", "!"),
    0x13: ("2", "@"),
    0x14: ("3", "#"),
    0x15: ("4", "$"),
    0x16: ("6", "^"),
    0x17: ("5", "%"),
    0x18: ("=", "+"),
    0x19: ("9", "("),
    0x1A: ("7", "&"),
    0x1B: ("-", "_"),
    0x1C: ("8", "*"),
    0x1D: ("0", ")"),
    0x1E: ("]", "}"),
    0x1F: ("o", "O"),
    0x20: ("u", "U"),
    0x21: ("[", "{"),
    0x22: ("i", "I"),
    0x23: ("p", "P"),
    0x25: ("l", "L"),
    0x26: ("j", "J"),
    0x27: ("'", '"'),
    0x28: ("k", "K"),
    0x29: (";", ":"),
    0x2A: ("\\", "|"),
    0x2B: (",", "<"),
    0x2C: ("/", "?"),
    0x2D: ("n", "N"),
    0x2E: ("m", "M"),
    0x2F: (".", ">"),
    0x32: ("`", "~"),
}


def _normalize_accelerator(keyval, event_state, consumed_modifiers):
    """Return a GTK-compatible, caseless ``(keyval, modifiers)`` pair."""

    modifiers = Gdk.ModifierType(
        int(event_state)
        & int(Gtk.accelerator_get_default_mod_mask())
        & ~int(consumed_modifiers)
    )
    keyval_lower = Gdk.keyval_to_lower(keyval)
    if keyval_lower != keyval:
        modifiers |= Gdk.ModifierType.SHIFT_MASK
    return keyval_lower, modifiers


def _macos_physical_accelerator(event):
    """Return the ANSI-US accelerator at a macOS physical key position."""

    levels = _MACOS_ANSI_KEY_LEVELS.get(event.hardware_keycode)
    if levels is None:
        return None

    shifted = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
    char = levels[1 if shifted else 0]
    keyval = Gdk.unicode_to_keyval(ord(char))

    # Shift selects level 1 for every entry in the table.  Normalization adds
    # it back for letters (Shift+S) but not for symbols whose keyval already
    # expresses the shifted character (plus rather than Shift+equal).
    return _normalize_accelerator(
        keyval,
        event.state,
        Gdk.ModifierType.SHIFT_MASK,
    )


def _is_latin_or_ascii_keyval(keyval):
    """Whether a keyval is already suitable for a Latin shortcut map."""

    codepoint = Gdk.keyval_to_unicode(keyval)
    if not codepoint:
        return True
    char = chr(codepoint)
    if char.isascii():
        return True
    return "LATIN" in unicodedata.name(char, "")


def get_key_event_accelerators(event, keymap=None):
    """Return accelerators represented by a GDK key event, in priority order.

    The first item always represents the active keyboard layout.  On macOS a
    second item maps the same physical key position to ANSI-US, allowing the
    traditional Latin shortcuts to keep working in non-Latin input sources.
    """

    if keymap is None:
        keymap = Gdk.Keymap.get_default()

    state = Gdk.ModifierType(
        int(event.state) & ~int(Gdk.ModifierType.LOCK_MASK)
    )
    # Keep the workaround for the Windows/Japanese IME issue for which the
    # group=1 override was introduced.  On other backends, and especially on
    # Quartz where group 1 is the Option layer, the event group is required.
    group = 1 if sys.platform == "win32" else event.group
    result = keymap.translate_keyboard_state(
        event.hardware_keycode,
        state,
        group,
    )
    if not result:
        return []

    keyval = result[1]
    consumed_modifiers = result[4]
    accelerators = [
        _normalize_accelerator(keyval, event.state, consumed_modifiers)
    ]

    if sys.platform == "darwin":
        physical = _macos_physical_accelerator(event)
        if physical is not None and physical not in accelerators:
            accelerators.append(physical)

    return accelerators


def get_key_event_accelerator(
    event,
    keymap=None,
    prefer_physical_non_latin=False,
):
    """Return the preferred accelerator for displaying or saving a binding.

    ``prefer_physical_non_latin`` makes an unmodified non-Latin character use
    the macOS physical fallback.  This keeps the shortcut editor stable across
    Latin and non-Latin input sources while still allowing Option-generated
    characters such as ``ß`` to be assigned explicitly.
    """

    accelerators = get_key_event_accelerators(event, keymap)
    if not accelerators:
        return None
    if (
        prefer_physical_non_latin
        and len(accelerators) > 1
        and not event.state & Gdk.ModifierType.MOD1_MASK
        and not _is_latin_or_ascii_keyval(accelerators[0][0])
    ):
        return accelerators[-1]
    return accelerators[0]
