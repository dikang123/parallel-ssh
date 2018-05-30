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

from gevent import socket, spawn, joinall, get_hub

from ssh2.error_codes import LIBSSH2_ERROR_EAGAIN

from .clients.native.single import SSHClient
from .native._ssh2 import wait_select
from .constants import DEFAULT_RETRIES, RETRY_DELAY


logger = logging.getLogger(__name__)


class Tunnel(Thread):

    def __init__(self, host, fw_host, fw_port, user=None,
                 password=None, port=None, pkey=None,
                 num_retries=DEFAULT_RETRIES,
                 retry_delay=RETRY_DELAY,
                 allow_agent=True, timeout=None, listen_port=0):
        Thread.__init__(self)
        self.client = None
        self.session = None
        self.socket = None
        self.listen_port = listen_port
        self.fw_host = fw_host
        self.fw_port = fw_port if fw_port else 22
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
                    wait_select(channel.session)
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
                    logger.debug("Waiting on channel")
                    wait_select(channel.session)
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
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(('127.0.0.1', self.listen_port))
        self.socket.listen(0)
        self.listen_port = self.socket.getsockname()[1]
        logger.debug("Tunnel listening on 127.0.0.1:%s on hub %s",
                     self.listen_port, get_hub())

    def _init_tunnel_client(self):
        self.client = SSHClient(self.host, user=self.user, port=self.port,
                                password=self.password, pkey=self.pkey,
                                num_retries=self.num_retries,
                                retry_delay=self.retry_delay,
                                allow_agent=self.allow_agent,
                                timeout=self.timeout)
        self.session = self.client.session

    def _cleanup(self):
        self.session.set_blocking(1)
        self.session.disconnect()
        del self.session
        del self.client
        self.client = None
        self.session = None

    def run(self):
        try:
            self._init_tunnel_client()
            self._init_tunnel_sock()
        except Exception as ex:
            logger.error("Tunnel initilisation failed with %s", ex)
            self.exception = ex
            return
        logger.debug("Hub in run function: %s", get_hub())
        try:
            while True:
                logger.debug("Tunnel waiting for connection")
                self.tunnel_open.set()
                forward_sock, forward_addr = self.socket.accept()
                logger.debug("Client connected, forwarding %s:%s on"
                             " remote host to local %s",
                             self.fw_host, self.fw_port, forward_addr)
                try:
                    # self.session.set_blocking(0)
                    channel = self.session.direct_tcpip_ex(
                        self.fw_host, self.fw_port, '127.0.0.1',
                        forward_addr[1])
                    while channel == LIBSSH2_ERROR_EAGAIN:
                        wait_select(self.session)
                        channel = self.session.direct_tcpip_ex(
                            self.fw_host, self.fw_port, '127.0.0.1',
                            forward_addr[1])
                except Exception as ex:
                    logger.error("Could not establish channel to %s:%s - %s",
                                 self.fw_host, self.fw_port, ex)
                    self.exception = ex
                    continue
                # self.session.set_blocking(0)
                source = spawn(self._read_forward_sock, forward_sock, channel)
                dest = spawn(self._read_channel, forward_sock, channel)
                joinall((source, dest), raise_error=True)
                channel.close()
                forward_sock.close()
        except Exception as ex:
            msg = "Tunnel caught exception - %s"
            logger.error(msg, ex)
            self.exception = ex
        finally:
            if not self.socket.closed:
                self.socket.close()
            self._cleanup()
