#!/usr/bin/env python
# Copyright (C) 2012 Christopher Kaster
# This file is part of BooruPy Loadr
#
# You should have received a copy of the GNU General Public License
# along with BooruPy Loadr. If not, see <http://www.gnu.org/licenses/>
import pygtk
pygtk.require("2.0")
import gtk
import gtk.glade
import gobject
import sys
import os
import urllib2
import hashlib
import glib
from threading import Thread, Event
from BooruPy.booru import BooruPy
from os.path import join, basename, dirname, abspath
from Queue import Queue, Empty


class UiActions(object):
    image = 0
    file_progress = 1


class BooruPyLoadr():

    def __init__(self, providerlist, gladefilepath):
        self._providerlist = providerlist
        self._gladefile = gladefilepath
        self._widget_tree = gtk.glade.XML(self._gladefile)
        self.stop_event = Event()

        # get gui elements
        self._window = self._widget_tree.get_widget("bpyloadr_window")
        self._provider_field = self._widget_tree.get_widget("provider_field")
        self._tags_field = self._widget_tree.get_widget("tags_field")
        self._filepath_field = self._widget_tree.get_widget("filepath_field")

        self._btn_get = self._widget_tree.get_widget("btn_get")
        self._btn_stop = self._widget_tree.get_widget("btn_stop")
        self._btn_stop.set_sensitive(False)

        self._lbl_progress = self._widget_tree.get_widget("lbl_progress")
        self._total_progress = self._widget_tree.get_widget("total_progress")
        self._image_field = self._widget_tree.get_widget("latest_image")

        # cell renderer
        self._cell_renderer = gtk.CellRendererText()
        self._provider_field.pack_start(self._cell_renderer, True)
        self._provider_field.add_attribute(
        self._cell_renderer, 'text', 0)

        # create model for provider
        self._provider_model = gtk.ListStore(gobject.TYPE_STRING)

        # booruPy
        self._booru_handler = BooruPy(self._providerlist)

        # fill provider list
        for item in self._booru_handler.provider_list:
            self._add_provider(item.name)

        self._provider_field.set_model(self._provider_model)

        self._window.connect("destroy", self._quit)
        self._btn_get.connect("clicked",
            self.btn_get_clicked,
            self._window)
        self._btn_stop.connect("clicked",
            self.btn_stop_clicked,
            self._window)

        self._init_ui_worker_thread()
        glib.idle_add(self._ui_idle_change)

    def _ui_idle_change(self):
        try:
            item = self.ui_queue.get_nowait()
        except Empty:
            return True
        if item[0] == UiActions.image:
            self._image_field.set_from_pixbuf(item[1])
        elif item[0] == UiActions.file_progress:
            self._lbl_progress.set_text(item[1])
            self._total_progress.set_fraction(item[2])
        return True

    def _init_ui_worker_thread(self):
        self._ui_worker_thread = UiWorker()
        self.ui_queue = self._ui_worker_thread.ui_queue
        self.ui_worker_queue = self._ui_worker_thread.ui_worker_queue
        self._ui_worker_thread.start()

    def show(self):
        self._window.show_all()
        gtk.gdk.threads_init()
        gtk.main()

    def _quit(self, event):
        gtk.main_quit()
        sys.exit()

    def _add_provider(self, provider_name):
        self._provider_model.append([provider_name])

    def get_provider(self):
        provider_id = self._provider_field.get_active()
        return self._booru_handler.get_provider_by_id(int(provider_id))

    def get_tags(self):
        return self._tags_field.get_text().split(' ')

    def get_filepath(self):
        path = self._filepath_field.get_current_folder()

        if not path[-1] is "/":
            path += "/"

        return path

    def toggle_button(self):
        sensitive = self._btn_get.get_sensitive()
        self._btn_get.set_sensitive(False if sensitive else True)
        self._btn_stop.set_sensitive(sensitive)

    def btn_get_clicked(self, widget, data=None):
        self.toggle_button()
        self.stop_event.clear()
        thread = Thread(target=self._download)
        thread.daemon = True
        thread.start()

    def btn_stop_clicked(self, widget, data=None):
        self.toggle_button()
        self.stop_event.set()

    def _get_md5_checksum_from_file(self, path):
        _file = open(path, 'rb')
        md5 = hashlib.md5()
        while True:
            data = _file.read(8192)
            if not data:
                break
            md5.update(data)
        return md5.hexdigest()

    def _download(self):
        provider = self.get_provider()
        tags = self.get_tags()
        folder_name = "%s-%s" % (provider.shortname, "-".join(tags))

        path = join(self.get_filepath(), folder_name)
        if not os.path.exists(path):
            os.mkdir(path)

        for i in provider.get_images(tags):
            if self.stop_event.is_set():
                return
            file_name = "%s-%s[%s].%s" % (
                provider.shortname,
                '-'.join(tags),
                i.md5,
                i.url.split('.')[-1])

            target_path = join(path, file_name)

            # check file md5 checksum
            if os.path.exists(target_path):
                filemd5 = self._get_md5_checksum_from_file(target_path)
                if i.md5 == filemd5:
                    continue

            res = urllib2.urlopen(i.url)
            _file = open(target_path, 'wb')
            meta = res.info()
            file_size = int(meta.getheaders("Content-Length")[0])

            file_size_dl = 0
            block_sz = 8192

            status = ShowStatusTask(self.ui_worker_queue, target_path)

            while True:
                buffer = res.read(block_sz)
                if not buffer:
                    status.finished()
                    break
                file_size_dl += len(buffer)
                _file.write(buffer)
                status.report_progress(file_size_dl * 100 / file_size)
        self.toggle_button()


class ShowStatusTask(object):
    def __init__(self, ui_queue, file_path):
        self._queue = ui_queue
        self.FilePath = file_path
        self.FileName = basename(file_path)
        self.PercentageDone = 0
        self.is_done = False
        self._queue.put(self)

    def report_progress(self, percentage_done):
        self.PercentageDone = percentage_done
        self._queue.put(self)

    def finished(self):
        self.PercentageDone = 100
        self.is_done = True
        self._queue.put(self)

    def get_status_message(self):
        return "Donloading %s [%3.2f%%]" % (self.FileName, self.PercentageDone)


class UiWorker(Thread):

    def __init__(self):
        super(UiWorker, self).__init__()
        self.daemon = True

        self.ui_queue = Queue()
        self.ui_worker_queue = Queue()

    def run(self):
        while "there is stuff to do":
            task = self.ui_worker_queue.get()
            if task.is_done:
                try:
                    self._report_progress(task)
                    pb = self._resize_image(task.FilePath)
                    self._show_image(pb)
                except:
                    print("Unexpected error:", sys.exc_info())
            else:
                self._report_progress(task)

    def _report_progress(self, task):
        value = float(task.PercentageDone) / 100
        self.ui_queue.put((UiActions.file_progress,
            task.get_status_message(),
            value))

    def _resize_image(self, path):
        pixbuf = gtk.gdk.pixbuf_new_from_file(path)

        pic_height = pixbuf.get_height()
        pic_width = pixbuf.get_width()

        factor = pic_height / 300

        pic_height = pic_height // factor
        pic_width = pic_width // factor

        pb = pixbuf.scale_simple(pic_width,
                pic_height,
                gtk.gdk.INTERP_BILINEAR)
        return pb

    def _show_image(self, pb):
        self.ui_queue.put((UiActions.image, pb))

if __name__ == "__main__":
    provider = dirname(abspath(sys.argv[0])) + "/data/provider.json"
    gladefile = dirname(abspath(sys.argv[0])) + "/data/gui.glade"

    app = BooruPyLoadr(provider, gladefile)
    app.show()
