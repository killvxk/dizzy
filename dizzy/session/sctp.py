#       sctp.py
#
#       Copyright 2017 Daniel Mende <mail@c0decafe.de>
#

#       Redistribution and use in source and binary forms, with or without
#       modification, are permitted provided that the following conditions are
#       met:
#
#       * Redistributions of source code must retain the above copyright
#         notice, this list of conditions and the following disclaimer.
#       * Redistributions in binary form must reproduce the above
#         copyright notice, this list of conditions and the following disclaimer
#         in the documentation and/or other materials provided with the
#         distribution.
#       * Neither the name of the  nor the names of its
#         contributors may be used to endorse or promote products derived from
#         this software without specific prior written permission.
#
#       THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#       "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#       LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
#       A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
#       OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
#       SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
#       LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
#       DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
#       THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
#       (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#       OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
from . import SessionException, SessionParseException
from socket import inet_aton, AF_INET, inet_pton, AF_INET6, htonl, SOCK_SEQPACKET, SOL_SOCKET, SO_BROADCAST, SO_SNDBUF,\
    SO_REUSEADDR, socket
from traceback import print_exc
from ctypes import Structure, c_uint16, c_uint32, c_int
from dizzy.log import print_dizzy, DEBUG

class sctp_sndrcvinfo(Structure):
    _fields_ = [("sinfo_stream", c_uint16),
                ("sinfo_ssn", c_uint16),
                ("sinfo_flags", c_uint16),
                ("sinfo_ppid", c_uint32),
                ("sinfo_context", c_uint32),
                ("sinfo_timetolive", c_uint32),
                ("sinfo_tsn", c_uint32),
                ("sinfo_cumtsn", c_uint32),
                ("sinfo_assoc_id", c_int)]

class DizzySession(object):
    SCTP_STREAM = 1
    SCTP_PPID = 1
    SCTP_FLAGS = 0 #MSG_ADDR_OVER ?
    SOL_SCTP = 0x84
    IPPROTO_SCTP = 0x84
    SCTP_DEFAULT_SEND_PARAM = 0xa
    SCTP_SNDRCV = 1

    def __init__(self, section_proxy):
        self.dest = section_proxy.get('target_host')
        self.dport = section_proxy.getint('target_port')
        self.src = section_proxy.get('source_host', '')
        self.sport = section_proxy.getint('source_port')
        self.timeout = section_proxy.getfloat('timeout', 1)
        self.recv_buffer = section_proxy.getfloat('recv_buffer', 4096)
        self.auto_reopen = section_proxy.getboolean('auto_reopen', True)
        self.server_side = section_proxy.getboolean('server', False)
        self.read_first = self.server_side
        self.read_first = section_proxy.getboolean('read_first', self.read_first)
        self.connect_retry = section_proxy.getint('retry', 3)
        self.sid = section_proxy.getint('streamid', self.SCTP_STREAM)
        self.ppid = section_proxy.getint('ppid', self.SCTP_PPID)
        self.is_open = False

        try:
            inet_aton(self.dest)
            self.af = AF_INET
        except Exception as e:
            try:
                inet_pton(AF_INET6, self.dest)
                self.af = AF_INET6
            except Exception as f:
                raise SessionParseException("unknown address family: %s: %s, %s" % (self.dest, e, f))
        if self.src != '':
            try:
                inet_aton(self.src)
            except Exception as e:
                try:
                    inet_pton(AF_INET6, self.src)
                except Exception as f:
                    raise SessionParseException("unknown address family: %s: %s, %s" % (self.src, e, f))
                else:
                    if not self.af == AF_INET6:
                        raise SessionParseException("address family missmatch: %s - %s" % (self.dest, self.src))
            else:
                if not self.af == AF_INET:
                    raise SessionParseException("address family missmatch: %s - %s" % (self.dest, self.src))

        self.sndrcvinfo = sctp_sndrcvinfo()
        self.sndrcvinfo.sinfo_stream = self.sid
        self.sndrcvinfo.sinfo_ppid = htonl(self.ppid)
        self.cs = None
        self.maxsize = 65534

    def open(self):
        try:
            self.s = socket(self.af, SOCK_SEQPACKET)
            if self.dest == "255.255.255.255":
                self.s.setsockopt(SOL_SOCKET, SO_BROADCAST, 1)
            self.s.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
            self.s.settimeout(self.timeout)
            sendbuf = self.s.getsockopt(SOL_SOCKET, SO_SNDBUF)
            if sendbuf < self.maxsize:
                self.maxsize = sendbuf
            self.s.setsockopt(self.SOL_SCTP, self.SCTP_DEFAULT_SEND_PARAM, self.sndrcvinfo)
            if not self.sport is None:
                self.s.bind((self.src, self.sport))
        except Exception as e:
            raise SessionException("cant open session: %s" % str(e))
        else:
            self.is_open = True

    def close(self):
        if not self.is_open:
            return
        if not self.s is None:
            self.s.close()
            self.s = None
        if not self.cs is None:
            self.cs.close()
            self.cs = None
        self.is_open = False

    def send(self, data, streamid=None):
        try:
            if not self.maxsize is None and len(data) > self.maxsize:
                data = data[:self.maxsize - 1]
                print_dizzy("session/sctp: Truncated data to %d byte." % self.maxsize, DEBUG)
            if not streamid is None:
                sndrcvinfo = sctp_sndrcvinfo()
                sndrcvinfo.sinfo_stream = streamid
                sndrcvinfo.sinfo_ppid = htonl(self.ppid)
                self.s.sendmsg([data], [(self.IPPROTO_SCTP, self.SCTP_SNDRCV, sndrcvinfo)], 0, (self.dest, self.dport))
            else:
                self.s.sendto(data, (self.dest, self.dport))
        except Exception as e:
            if self.auto_reopen:
                print_dizzy("session/sctp: session got closed '%s', autoreopening..." % e, DEBUG)
                self.close()
                self.open()
            else:
                self.close()
                raise SessionException("error on sending '%s', connection closed." % e)

    def recv(self):
        if self.server_side:
            return self.cs.recv(self.recv_buffer)
        else:
            return self.s.recv(self.recv_buffer)
