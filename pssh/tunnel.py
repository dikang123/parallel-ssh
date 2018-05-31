# This file is part of parallel-ssh.

# Copyright (C) 2014-2018 Panos Kittenis.

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

from threading import Thread, Event
import logging

from gevent import socket, spawn, joinall, get_hub, sleep

from ssh2.error_codes import LIBSSH2_ERROR_EAGAIN

from .clients.native.single import SSHClient
from .native._ssh2 import wait_select
from .constants import DEFAULT_RETRIES, RETRY_DELAY


logger = logging.getLogger(__name__)


class Tunnel(Thread):

    def __init__(self, host, in_q, out_q, user=None,
                 password=None, port=None, pkey=None,
                 num_retries=DEFAULT_RETRIES,
                 retry_delay=RETRY_DELAY,
                 allow_agent=True, timeout=None):
        Thread.__init__(self)
        self.client = None
        self.session = None
        self._sockets = []
        self.in_q = in_q
        self.out_q = out_q
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.pkey = pkey
        self.num_retries = num_retries
        self.retry_delay = retry_delay
        self.allow_agent = allow_agent
        self.timeout = timeout
        self.exception = None
        self.tunnel_open = Event()
        self.tunnels = []

    def _read_forward_sock(self, forward_sock, channel):
        while True:
            logger.debug("Waiting on forward socket read")
            data = forward_sock.recv(1024)
            data_len = len(data)
            if data_len == 0:
                continue
                # logger.error("Client disconnected")
                # return
            data_written = 0
            rc = channel.write(data)
            while data_written < data_len:
                if rc == LIBSSH2_ERROR_EAGAIN:
                    logger.debug("Waiting on channel write")
                    wait_select(channel.session, timeout=.001)
                    rc = channel.write(data)
                    continue
                elif rc < 0:
                    logger.error("Channel write error %s", rc)
                    return
                data_written += rc
                logger.debug(
                    "Wrote %s bytes from forward socket to channel", rc)
                rc = channel.write(data[data_written:])
            logger.debug("Total channel write size %s from %s received",
                         data_written, data_len)

    def _read_channel(self, forward_sock, channel):
        while True:
            size, data = channel.read()
            while size == LIBSSH2_ERROR_EAGAIN or size > 0:
                if size == LIBSSH2_ERROR_EAGAIN:
                    logger.debug("Waiting on channel read")
                    wait_select(channel.session, timeout=.001)
                    size, data = channel.read()
                elif size < 0:
                    logger.error("Error reading from channel")
                    return
                while size > 0:
                    logger.debug("Read %s from channel..", size)
                    forward_sock.sendall(data)
                    logger.debug("Forwarded %s bytes from channel", size)
                    size, data = channel.read()
            # if channel.eof():
            #     logger.debug("Channel closed")
            #     return

    def _init_tunnel_sock(self):
        tunnel_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tunnel_socket.bind(('127.0.0.1', 0))
        tunnel_socket.listen(0)
        listen_port = tunnel_socket.getsockname()[1]
        self._sockets.append(tunnel_socket)
        return tunnel_socket, listen_port

    def _init_tunnel_client(self):
        self.client = SSHClient(self.host, user=self.user, port=self.port,
                                password=self.password, pkey=self.pkey,
                                num_retries=self.num_retries,
                                retry_delay=self.retry_delay,
                                allow_agent=self.allow_agent,
                                timeout=self.timeout)
        self.session = self.client.session

    def _cleanup(self):
        for _sock in self._sockets:
            try:
                _sock.close()
            except Exception as ex:
                logger.error(ex)
        if self.session is not None:
            self.session.set_blocking(1)
            self.session.disconnect()
            del self.session
            del self.client
            self.client = None
            self.session = None

    def _consume_q(self):
        host, port = self.in_q.get()
        logger.debug("Got request for tunnel to %s:%s", host, port)
        tunnel = spawn(self._start_tunnel, host, port)
        self.tunnels.append(tunnel)
        tunnel.get()

    def _start_tunnel(self, fw_host, fw_port):
        listen_socket, listen_port = self._init_tunnel_sock()
        self.out_q.put(listen_port)
        self.tunnel_open.set()
        logger.debug("Tunnel listening on 127.0.0.1:%s on hub %s",
                     listen_port, get_hub().thread_ident)
        forward_sock, forward_addr = listen_socket.accept()
        logger.debug("Client connected, forwarding %s:%s on"
                     " remote host to local %s",
                     fw_host, fw_port, forward_addr)
        try:
            self.session.set_blocking(1)
            channel = self.session.direct_tcpip_ex(
                fw_host, fw_port, '127.0.0.1',
                forward_addr[1])
            while channel == LIBSSH2_ERROR_EAGAIN:
                wait_select(self.session)
                channel = self.session.direct_tcpip_ex(
                    fw_host, fw_port, '127.0.0.1',
                    forward_addr[1])
        except Exception as ex:
            logger.error("Could not establish channel to %s:%s - %s",
                         fw_host, fw_port, ex)
            self.exception = ex
            return
        finally:
            self.session.set_blocking(0)
        logger.debug("Spawning read/write greenlets")
        source = spawn(self._read_forward_sock, forward_sock, channel)
        dest = spawn(self._read_channel, forward_sock, channel)
        logger.debug("Waiting for read/write greenlets")
        joinall((source, dest), raise_error=True)
        channel.close()
        forward_sock.close()

    def run(self):
        try:
            self._init_tunnel_client()
        except Exception as ex:
            logger.error("Tunnel initilisation failed with %s", ex)
            self.exception = ex
            return
        logger.debug("Hub ID in run function: %s", get_hub().thread_ident)
        while True:
            try:
                self._consume_q()
            except Exception as ex:
                msg = "Tunnel caught exception - %s"
                logger.error(msg, ex)
                self.exception = ex
            finally:
                self._cleanup()
