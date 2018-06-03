# This file is part of parallel-ssh.

# Copyright (C) 2015-2018 Panos Kittenis

# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation, version 2.1.

# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA

from __future__ import print_function

import unittest
import pwd
import logging
import os
import shutil
import sys
import string
from socket import timeout as socket_timeout
from sys import version_info
import random
import time
from collections import deque

from gevent import sleep
from pssh.tunnel import Tunnel
# from pssh.clients.native.single import SSHClient
# from pssh.clients.native import ParallelSSHClient
from pssh.exceptions import UnknownHostException, \
    AuthenticationException, ConnectionErrorException, SessionError, \
    HostArgumentException, SFTPError, SFTPIOError, Timeout, SCPError, \
    ProxyError
from pssh import logger as pssh_logger

from .embedded_server.embedded_server import make_socket
from .embedded_server.openssh import OpenSSHServer
from .base_ssh2_test import PKEY_FILENAME, PUB_FILE


pssh_logger.setLevel(logging.DEBUG)
logging.basicConfig()


class ParallelSSHClientTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _mask = int('0600') if version_info <= (2,) else 0o600
        os.chmod(PKEY_FILENAME, _mask)
        cls.server = OpenSSHServer()
        cls.server.start_server()
        cls.host = '127.0.0.1'
        cls.port = 2222
        cls.cmd = 'echo me'
        cls.resp = u'me'
        cls.user_key = PKEY_FILENAME
        cls.user_pub_key = PUB_FILE
        cls.user = pwd.getpwuid(os.geteuid()).pw_name

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()
        del cls.server

    def test_tunnel(self):
        proxy_host = '127.0.0.9'
        server = OpenSSHServer(listen_ip=proxy_host, port=self.port)
        server.start_server()
        in_q, out_q = deque(), deque()
        tunnel = Tunnel(proxy_host, in_q, out_q, port=self.port,
                        pkey=self.user_key, num_retries=1, timeout=1)
        tunnel.daemon = True
        tunnel.start()
        in_q.append((self.host, self.port))
        while not tunnel.tunnel_open.is_set():
            sleep(.1)
            if not tunnel.is_alive():
                raise ProxyError
        self.assertTrue(tunnel.tunnel_open.is_set())
        self.assertIsNotNone(tunnel.client)
        tunnel.cleanup()
        self.assertIsNone(tunnel.client)
        self.assertIsNone(tunnel.session)
        for _sock in tunnel._sockets:
            self.assertTrue(_sock.closed)
