# -*- coding: utf-8 -*-
"""Dex Hub's disc server in a process of its own (v5.10.584).

discserver serves a Blu-ray image's files to Kodi on 127.0.0.1. Inside Kodi's Python it froze
Kodi for minutes when a disc was stopped: Kodi's interface thread waits for the disc's reader
as it closes the player, the reader waited for this server, and the server waited for Kodi's
Python, held by an add-on thread that was itself waiting for the interface thread (an add-on
asking Kodi for an info label holds Python while it waits). No line was logged by any add-on
meanwhile, and Kodi's reader asked again every 20 s, ten times (CoreELEC box, 17-18 Nov 2026).

Here the same code (discserver's handler, its small-file cache and kept connections) runs in a
Python started by the service, outside Kodi: it answers whatever Kodi's own Python is doing, so
a stopped disc closes at once. Kodi's side still reads each image's file system and writes it to
discs.json (offer_image); this process reads that file when Kodi asks for a disc it does not
know yet. A disc whose files are links of a Debrid service (discserver.offer) stays with the
server inside Kodi, which has the accounts.

Run as: python3 -I discproc.py <addon data folder> <port file> <Kodi's process id>
It leaves when Kodi's process is gone or the port file is removed.
"""
import os
import sys
import threading
import time
import types


def main():
    folder, port_file, parent = sys.argv[1], sys.argv[2], int(sys.argv[3])
    here = os.path.dirname(os.path.abspath(__file__))
    package = types.ModuleType('dexhub_disc')
    package.__path__ = [here]
    sys.modules['dexhub_disc'] = package
    from dexhub_disc import accounts
    accounts.PATH_OVERRIDE = folder
    log_path = os.path.join(folder, 'discproc.log')
    log_lock = threading.Lock()

    def log(msg, warning=False):
        line = '%s %s%s\n' % (time.strftime('%H:%M:%S'), 'warning: ' if warning else '', msg)
        with log_lock:
            try:
                if os.path.exists(log_path) and os.path.getsize(log_path) > 512 * 1024:
                    os.replace(log_path, log_path + '.old')
                with open(log_path, 'a', encoding='utf-8') as handle:
                    handle.write(line)
            except Exception:
                pass

    accounts.log = log
    accounts.option = lambda name: True
    from dexhub_disc import discserver
    from http.server import ThreadingHTTPServer

    class Server(ThreadingHTTPServer):
        daemon_threads = True

        def handle_error(self, request, client_address):
            pass                # Kodi dropping a connection is not an error here

    server = Server((discserver.HOST, 0), discserver._handler())
    threading.Thread(target=server.serve_forever, name='disc-server', daemon=True).start()
    with open(port_file + '.tmp', 'w') as handle:
        handle.write(str(server.server_address[1]))
    os.replace(port_file + '.tmp', port_file)
    log('disc server started on port %d (Python %s, Kodi %d)'
        % (server.server_address[1], sys.version.split()[0], parent))
    while True:
        time.sleep(2.0)
        try:
            os.kill(parent, 0)
        except OSError:
            log('Kodi is gone: the disc server stops')
            break
        if not os.path.exists(port_file):
            log('the port file was removed: the disc server stops')
            break
    server.shutdown()


if __name__ == '__main__':
    main()
