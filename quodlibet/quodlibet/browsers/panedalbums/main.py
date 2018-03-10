# -*- coding: utf-8 -*-
# Copyright 2004-2008 Joe Wreschnig, Michael Urman, Iñigo Serna
#           2009,2010 Steven Robertson
#           2009-2013 Christoph Reiter
#           2011-2018 Nick Boultbee
#                2017 Fredrik Strupe
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

from re import sub

from gi.repository import Gtk, GLib

from quodlibet import config
from quodlibet import qltk
from quodlibet import util
from quodlibet import _
from quodlibet.browsers import Browser
from quodlibet.formats import PEOPLE
from quodlibet.qltk.songlist import SongList
from quodlibet.qltk.completion import LibraryTagCompletion
from quodlibet.qltk.searchbar import SearchBarBox
from quodlibet.qltk.x import ScrolledWindow, Align
from quodlibet.util.library import background_filter
from quodlibet.util import connect_destroy
from quodlibet.qltk.paned import ConfigMultiRHPaned

from .prefs import PreferencesButton
from .util import get_headers
from .pane import Pane

def sanitizeForQuery(s):
    """Sanitize string so you can put it in a query, from the docs:
    The search terms can’t use quotes ("), slashes (/), hashes (#), pipes (|), ampersands (&), or bangs (!); these characters have special meanings for advanced searches.
    https://quodlibet.readthedocs.io/en/latest/guide/searching.html#searching-a-specific-tag
    TODO: MUCH BETTER HANDLING!
    """
    s = sub('\(.*?\)', '', s)
    s = sub('".*?"', '', s)
    return s


class PanedAlbumsBrowser(Browser, util.InstanceTracker):
    """A Browser enabling "drilling down" of tracks by successive
    selections in multiple tag pattern panes (e.g. Genre / People / Album ).
    It presents available values (and track counts) for each pane's tag
    """

    name = _("Paned Albums Browser")
    accelerated_name = _("_Paned Albums Browser")
    keys = ["PanedAlbums", "PanedAlbumsBrowser"]
    priority = 3

    def pack(self, songpane):
        container = Gtk.HBox()
        self.show()
        container.pack_start(self, True, True, 0)
        self.main_box.pack2(songpane, True, False)
        return container

    def unpack(self, container, songpane):
        self.main_box.remove(songpane)
        container.remove(self)

    @classmethod
    def set_all_wide_mode(klass, value):
        for browser in klass.instances():
            browser.set_wide_mode(value)

    @classmethod
    def set_all_panes(klass):
        for browser in klass.instances():
            browser.refresh_panes()
            browser.fill_panes()

    def __init__(self, library):
        super(PanedAlbumsBrowser, self).__init__()
        self._register_instance()

        self._filter = lambda s: False
        self._library = library

        self.set_spacing(6)
        self.set_orientation(Gtk.Orientation.VERTICAL)

        completion = LibraryTagCompletion(library.librarian)
        self.accelerators = Gtk.AccelGroup()
        sbb = SearchBarBox(completion=completion,
                           accel_group=self.accelerators)
        sbb.connect('query-changed', self.__text_parse)
        sbb.connect('focus-out', self.__focus)
        self._sb_box = sbb

        align = Align(sbb, left=6, right=6, top=6)
        self.pack_start(align, False, True, 0)

        keyval, mod = Gtk.accelerator_parse("<Primary>Home")
        self.accelerators.connect(keyval, mod, 0, self.__select_all)
        select = Gtk.Button(label=_("Select _All"), use_underline=True)
        select.connect('clicked', self.__select_all)
        sbb.pack_start(select, False, True, 0)

        prefs = PreferencesButton(self)
        sbb.pack_start(prefs, False, True, 0)

        connect_destroy(library, 'changed', self.__changed)
        connect_destroy(library, 'added', self.__added)
        connect_destroy(library, 'removed', self.__removed)

        self.connect('destroy', self.__destroy)

        # contains the panes and the song list
        self.main_box = qltk.ConfigRPaned("browsers", "panedbrowser_pos", 0.4)
        self.pack_start(self.main_box, True, True, 0)

        self.multi_paned = ConfigMultiRHPaned("browsers",
                                              "panedbrowser_pane_widths")
        self.refresh_panes()

        for child in self.get_children():
            child.show_all()

    def __destroy(self, *args):
        del self._sb_box

    def set_wide_mode(self, do_wide):
        hor = Gtk.Orientation.HORIZONTAL
        ver = Gtk.Orientation.VERTICAL

        if do_wide:
            self.main_box.props.orientation = hor
            self.multi_paned.change_orientation(horizontal=False)
        else:
            self.main_box.props.orientation = ver
            self.multi_paned.change_orientation(horizontal=True)

    def _get_text(self):
        return self._sb_box.get_text()

    def _set_text(self, text):
        self._sb_box.set_text(text)

    def __focus(self, widget, *args):
        qltk.get_top_parent(widget).songlist.grab_focus()

    def __text_parse(self, bar, text):
        self.activate()

    def filter_text(self, text):
        self._set_text(text)
        self.activate()

    def get_filter_text(self):
        return self._get_text()

    def __select_all(self, *args):
        self._panes[-1].inhibit()
        for pane in self._panes:
            pane.set_selected(None, True)
        self._panes[-1].uninhibit()
        self._panes[-1].get_selection().emit('changed')

    def __added(self, library, songs):
        songs = list(filter(self._filter, songs))
        for pane in self._panes:
            pane.add(songs)
            songs = list(filter(pane.matches, songs))

    def __removed(self, library, songs, remove_if_empty=True):
        songs = list(filter(self._filter, songs))
        for pane in self._panes:
            pane.remove(songs, remove_if_empty)

    def __changed(self, library, songs):
        self.__removed(library, songs, False)
        self.__added(library, songs)
        self.__removed(library, [])

    def active_filter(self, song):
        # check with the search filter
        if not self._filter(song):
            return False

        # check if the selection is right in every pane
        for pane in self._panes:
            if not pane.matches(song):
                return False

        return True

    def activate(self):
        star = dict.fromkeys(SongList.star)
        star.update(self.__star)
        query = self._sb_box.get_query(star.keys())
        if query.is_parsable:
            self._filter = query.search
            songs = list(filter(self._filter, self._library))
            bg = background_filter()
            if bg:
                songs = list(filter(bg, songs))
            self._panes[0].fill(songs)

    def scroll(self, song):
        for pane in self._panes:
            pane.scroll(song)


    def goto_album(self, *args):
        """When double-clicking on a row of a pane which filter albums
        set search panes to go to that album by searching:
        For classical music:
            "<composer> <performer> <album>"
        For the rest:
            "<artist|albumartist> <album|albumsort>

        """
        pane, *rest = args
        sel = pane.get_selected()
        model, path = pane.get_selection().get_selected_rows()

        # Get info from the first song filtered
        songs = model.get_songs(path)
        song = next(iter(songs))

        query = ""

        genre = song["genre"] if "genre" in song.keys() else ""
        if genre.lower() == "classical":
            composer = song["composer"] if "composer" in song.keys() else ""
            performer = song["performer"] if "performer" in song.keys() else ""
            album = ""
            if "album" in song.keys():
                album = "album={}".format(sanitizeForQuery(song["album"]))
            elif "albumartist" in song.keys():
                album = "albumsort={}".format(sanitizeForQuery(song["albumsort"]))

            query = "&(composer={}, performer={}, {})" .format(
                sanitizeForQuery(composer),
                sanitizeForQuery(performer),
                album)
        else:
            artist = ""
            if "artist" in song.keys():
                artist = "artist={}".format(sanitizeForQuery(song["artist"]))
            elif "albumartist" in song.keys():
                artist = "albumartist={}".format(sanitizeForQuery(song["albumartist"]))

            album = ""
            if "album" in song.keys():
                album = "album={}".format(sanitizeForQuery(song["album"]))
            elif "albumartist" in song.keys():
                album = "albumsort={}".format(sanitizeForQuery(song["albumsort"]))

            query = "&({}, {})".format(artist, album)

        self._sb_box.set_text(query)
        self._sb_box.changed()

        pane.set_selected([])


    def refresh_panes(self):
        self.multi_paned.destroy()
        # For some reason on_click_lambda gets called when double clicking

        # Fill in the pane list. The last pane reports back to us.
        self._panes = [self]
        for header in reversed(get_headers()):
            on_click_lambda = None
            if "<album>" in header or "<albumsort>" in header:
                on_click_lambda = lambda *x: self.goto_album(*x)
            else:
                on_click_lambda = lambda *x: self.songs_activated()
            pane = Pane(self._library, header, self._panes[0])
            pane.connect('row-activated',
                         on_click_lambda)
            self._panes.insert(0, pane)
        self._panes.pop()  # remove self

        # Put the panes in scrollable windows
        sws = []
        for pane in self._panes:
            sw = ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_shadow_type(Gtk.ShadowType.IN)
            sw.add(pane)
            sws.append(sw)

        self.multi_paned.set_widgets(sws)
        self.multi_paned.show_all()
        self.main_box.pack1(self.multi_paned.get_paned(), True, False)

        self.__star = {}
        for p in self._panes:
            tags = [t for t in p.tags if not t.startswith("~#")]
            self.__star.update(dict.fromkeys(tags))

        self.set_wide_mode(config.getboolean("browsers", "pane_wide_mode"))

    def fill_panes(self):
        self._panes[-1].inhibit()
        self.activate()
        self._panes[-1].uninhibit()

    def make_pane_widths_equal(self):
        self.multi_paned.make_pane_widths_equal()

    def __get_filter_pane(self, key):
        """Get the best pane for filtering etc."""

        candidates = []
        for pane in self._panes:
            if (key in pane.tags or
                    (key in PEOPLE and "~people" in pane.tags)):
                candidates.append((len(pane.tags), pane))
        candidates.sort()
        return (candidates and candidates[0][1]) or None

    def can_filter_tag(self, tag):
        return (self.__get_filter_pane(tag) is not None)

    def can_filter_text(self):
        return True

    def filter(self, tag, values):
        filter_pane = self.__get_filter_pane(tag)

        for pane in self._panes:
            if pane is filter_pane:
                filter_pane.set_selected_by_tag(tag, values, True)
                return
            pane.set_selected([None], True)

    def unfilter(self):
        self._panes[-1].inhibit()
        for pane in self._panes:
            pane.set_selected(None, True)
        self._panes[-1].uninhibit()
        self._set_text("")
        self.activate()

    def list(self, key):
        filter_pane = self.__get_filter_pane(key)

        if filter_pane is None:
            return super(PanedAlbumsBrowser, self).list(key)

        for pane in self._panes:
            if pane is filter_pane:
                return list(filter_pane.list(key))
            pane.set_selected(None, True)
        return []

    def save(self):
        config.settext("browsers", "query_text", self._get_text())

        selected = []
        for pane in self._panes:
            selected.append(pane.get_restore_string())

        to_save = u"\n".join(selected)
        config.settext("browsers", "pane_selection", to_save)

    def restore(self):
        try:
            text = config.gettext("browsers", "query_text")
        except config.Error:
            pass
        else:
            self._set_text(text)

        selected = config.gettext("browsers", "pane_selection")
        if not selected:
            return

        for pane, string in zip(self._panes, selected.split(u"\n")):
            pane.parse_restore_string(string)

    def finalize(self, restored):
        config.settext("browsers", "query_text", u"")
        if not restored:
            self.fill_panes()

    def fill(self, songs):
        GLib.idle_add(self.songs_selected, list(songs))
