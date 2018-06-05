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

from gevent import sleep, spawn, Timeout as GTimeout
from pssh.clients.native.tunnel import Tunnel
from pssh.clients.native import SSHClient, ParallelSSHClient
from pssh.exceptions import UnknownHostException, \
    AuthenticationException, ConnectionErrorException, SessionError, \
    HostArgumentException, SFTPError, SFTPIOError, Timeout, SCPError, \
    ProxyError
from pssh import logger as pssh_logger

from .embedded_server.openssh import OpenSSHServer
from .base_ssh2_test import PKEY_FILENAME, PUB_FILE


pssh_logger.setLevel(logging.DEBUG)
logging.basicConfig()


class TunnelTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        _mask = int('0600') if version_info <= (2,) else 0o600
        os.chmod(PKEY_FILENAME, _mask)
        cls.host = '127.0.0.1'
        cls.port = 2222
        cls.cmd = 'echo me'
        cls.resp = u'me'
        cls.user_key = PKEY_FILENAME
        cls.user_pub_key = PUB_FILE
        cls.user = pwd.getpwuid(os.geteuid()).pw_name

    def test_tunnel(self):
        proxy_host = '127.0.0.9'
        server = OpenSSHServer(listen_ip=proxy_host, port=self.port)
        server.start()
        server.wait_for_port()
        in_q, out_q = deque(), deque()
        try:
            tunnel = Tunnel(proxy_host, in_q, out_q, port=self.port,
                            pkey=self.user_key, num_retries=1)
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
            for _sock in tunnel._sockets:
                self.assertTrue(_sock.closed)
        finally:
            server.terminate()

    def test_tunnel_channel_failure(self):
        proxy_host = '127.0.0.9'
        remote_host = '127.0.0.8'
        server = OpenSSHServer(listen_ip=proxy_host, port=self.port)
        remote_server = OpenSSHServer(listen_ip=remote_host, port=self.port)
        for _server in (server, remote_server):
            _server.start()
            _server.wait_for_port()
        in_q, out_q = deque(), deque()
        try:
            tunnel = Tunnel(proxy_host, in_q, out_q, port=self.port,
                            pkey=self.user_key, num_retries=1)
            tunnel.daemon = True
            tunnel.start()
            in_q.append((remote_host, self.port))
            while not tunnel.tunnel_open.is_set():
                sleep(.1)
                if not tunnel.is_alive():
                    raise ProxyError
            self.assertTrue(tunnel.tunnel_open.is_set())
            self.assertIsNotNone(tunnel.client)
            while True:
                try:
                    _port = out_q.pop()
                except IndexError:
                    sleep(.5)
                else:
                    break
            proxy_client = SSHClient(
                '127.0.0.1', pkey=self.user_key, port=_port,
                num_retries=1, _auth_thread_pool=False)
            tunnel.cleanup()
            spawn(proxy_client.execute, 'echo me')
            proxy_client.disconnect()
            self.assertTrue(proxy_client.sock.closed)
        finally:
            for _server in (remote_server, server):
                _server.terminate()

    def test_tunnel_server_failure(self):
        proxy_host = '127.0.0.9'
        remote_host = '127.0.0.8'
        server = OpenSSHServer(listen_ip=proxy_host, port=self.port)
        remote_server = OpenSSHServer(listen_ip=remote_host, port=self.port)
        for _server in (server, remote_server):
            _server.start_server()
        in_q, out_q = deque(), deque()
        try:
            tunnel = Tunnel(proxy_host, in_q, out_q, port=self.port,
                            pkey=self.user_key, num_retries=1)
            tunnel._init_tunnel_client()
            consume_let = spawn(tunnel._consume_q)
            in_q.append((remote_host, self.port))
            while not tunnel.tunnel_open.is_set():
                sleep(.1)
                if not tunnel.is_alive():
                    raise ProxyError
            self.assertTrue(tunnel.tunnel_open.is_set())
            self.assertIsNotNone(tunnel.client)
            while True:
                try:
                    _port = out_q.pop()
                except IndexError:
                    sleep(.5)
                else:
                    break
            proxy_client = spawn(
                SSHClient,
                '127.0.0.1', pkey=self.user_key, port=_port,
                num_retries=1, _auth_thread_pool=False)
            remote_server.stop()
            tunnel.cleanup()
            self.assertRaises(ConnectionErrorException, proxy_client.get)
        finally:
            for _server in (remote_server, server):
                _server.stop()

    def test_tunnel(self):
        remote_host = '127.0.0.8'
        proxy_host = '127.0.0.9'
        server = OpenSSHServer(listen_ip=proxy_host, port=self.port)
        remote_server = OpenSSHServer(listen_ip=remote_host, port=self.port)
        for _server in (server, remote_server):
            _server.start()
            _server.wait_for_port()
        try:
            client = ParallelSSHClient(
                [remote_host], port=self.port, pkey=self.user_key,
                proxy_host=proxy_host, proxy_port=self.port, num_retries=1,
                proxy_pkey=self.user_key)
            output = client.run_command(self.cmd)
            client.join(output)
            for host, host_out in output.items():
                _stdout = list(host_out.stdout)
                self.assertListEqual(_stdout, [self.resp])
            self.assertEqual(remote_host, list(output.keys())[0])
            del client
        finally:
            for _server in (remote_server, server):
                _server.terminate()

    def test_tunnel_init_failure(self):
        proxy_host = '127.0.0.20'
        client = ParallelSSHClient(
            [self.host], port=self.port, pkey=self.user_key,
            proxy_host=proxy_host, proxy_port=self.port, num_retries=1,
            proxy_pkey=self.user_key)
        output = client.run_command(self.cmd, stop_on_errors=False)
        client.join(output)
        exc = output[self.host].exception
        self.assertIsInstance(exc, ProxyError)
        self.assertIsInstance(exc.args[1], ConnectionErrorException)

    def test_tunnel_remote_host_timeout(self):
        remote_host = '127.0.0.8'
        proxy_host = '127.0.0.9'
        server = OpenSSHServer(listen_ip=proxy_host, port=self.port)
        remote_server = OpenSSHServer(listen_ip=remote_host, port=self.port)
        for _server in (server, remote_server):
            _server.start()
            _server.wait_for_port()
        try:
            client = ParallelSSHClient(
                [remote_host], port=self.port, pkey=self.user_key,
                proxy_host=proxy_host, proxy_port=self.port, num_retries=1,
                proxy_pkey=self.user_key,
                timeout=2)
            output = client.run_command(self.cmd)
            client.join(output)
            client._tunnel.cleanup()
            remote_server.stop()
            server.stop()
            # Gevent timeout cannot be caught by stop_on_errors
            self.assertRaises(GTimeout, client.run_command, self.cmd,
                              greenlet_timeout=1, stop_on_errors=False)
            del client
        finally:
            for _server in (remote_server, server):
                _server.terminate()

    def test_single_tunnel_multi_hosts(self):
        remote_host = '127.0.0.8'
        proxy_host = '127.0.0.39'
        server = OpenSSHServer(listen_ip=proxy_host, port=self.port)
        server.start()
        remote_server = OpenSSHServer(listen_ip=remote_host, port=self.port)
        remote_server.start()
        hosts = [remote_host, remote_host, remote_host]
        try:
            client = ParallelSSHClient(
                hosts, port=self.port, pkey=self.user_key,
                proxy_host=proxy_host, proxy_port=self.port, num_retries=1,
                proxy_pkey=self.user_key)
            output = client.run_command(self.cmd, stop_on_errors=False)
            client.join(output)
            for host, host_out in output.items():
                _stdout = list(host_out.stdout)
                self.assertListEqual(_stdout, [self.resp])
            self.assertEqual(len(hosts), len(list(output.keys())))
            del client
        finally:
            for _server in (remote_server, server):
                _server.terminate()

    def test_single_tunnel_multi_hosts_timeout(self):
        remote_host = '127.0.0.8'
        proxy_host = '127.0.0.29'
        server = OpenSSHServer(listen_ip=proxy_host, port=self.port)
        server.start()
        remote_server = OpenSSHServer(listen_ip=remote_host, port=self.port)
        remote_server.start()
        hosts = [remote_host, remote_host, remote_host]
        try:
            client = ParallelSSHClient(
                hosts, port=self.port, pkey=self.user_key,
                proxy_host=proxy_host, proxy_port=self.port, num_retries=1,
                proxy_pkey=self.user_key,
                timeout=.001)
            output = client.run_command(self.cmd, stop_on_errors=False)
            client.join(output)
            for host, host_out in output.items():
                self.assertIsInstance(output[host].exception, Timeout)
        finally:
            for _server in (remote_server, server):
                _server.terminate()
