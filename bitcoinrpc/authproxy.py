
"""
  Copyright 2011 Jeff Garzik

  AuthServiceProxy has the following improvements over python-jsonrpc's
  ServiceProxy class:

  - HTTP connections persist for the life of the AuthServiceProxy object
    (if server supports HTTP/1.1)
  - sends protocol 'version', per JSON-RPC 1.1
  - sends proper, incrementing 'id'
  - sends Basic HTTP authentication headers
  - parses all JSON numbers that look like floats as Decimal
  - uses standard Python json lib

  Previous copyright, from python-jsonrpc/jsonrpc/proxy.py:

  Copyright (c) 2007 Jan-Klaas Kollhof

  This file is part of jsonrpc.

  jsonrpc is free software; you can redistribute it and/or modify
  it under the terms of the GNU Lesser General Public License as published by
  the Free Software Foundation; either version 2.1 of the License, or
  (at your option) any later version.

  This software is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU Lesser General Public License for more details.

  You should have received a copy of the GNU Lesser General Public License
  along with this software; if not, write to the Free Software
  Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
"""

import base64
import decimal
import json
import logging
import urllib3
from urllib3.util.timeout import Timeout
from urllib3.util.request import make_headers

try:
    import urllib.parse as urlparse
except ImportError:
    import urlparse

USER_AGENT = "AuthServiceProxy/0.1"

HTTP_TIMEOUT = 30

log = logging.getLogger("BitcoinRPC")

class JSONRPCException(Exception):
    def __init__(self, rpc_error):
        Exception.__init__(self)
        self.error = rpc_error


def EncodeDecimal(o):
    if isinstance(o, decimal.Decimal):
        return round(o, 8)
    raise TypeError(repr(o) + " is not JSON serializable")

class AuthServiceProxy(object):
    __id_count = 0

    def __init__(self, service_url, service_name=None, timeout=HTTP_TIMEOUT, connection=None):
        self.__service_url = service_url
        self.__service_name = service_name
        self.__url = urlparse.urlparse(service_url)
        self.__auth = '%s:%s' % (self.__url.username, self.__url.password)
        self.__headers = make_headers(keep_alive=True, user_agent=USER_AGENT, basic_auth=self.__auth)
        self.__headers.update({'Content-Type:': 'application/json'})

        self.__conn = urllib3.PoolManager(retries=False, timeout=Timeout(timeout))

    def __getattr__(self, name):
        if name.startswith('__') and name.endswith('__'):
            # Python internal stuff
            raise AttributeError
        if self.__service_name is not None:
            name = "%s.%s" % (self.__service_name, name)
        return AuthServiceProxy(self.__service_url, name, connection=self.__conn)

    def __call__(self, *args):
        AuthServiceProxy.__id_count += 1

        log.debug("-%s-> %s %s"%(AuthServiceProxy.__id_count, self.__service_name,
                                 json.dumps(args, default=EncodeDecimal)))
        postdata = json.dumps({'version': '1.1',
                               'method': self.__service_name,
                               'params': args,
                               'id': AuthServiceProxy.__id_count}, default=EncodeDecimal)
        http_response = self.__conn.urlopen('POST', self.__service_url, body=postdata, headers=self.__headers)

        response = self._parse_response(http_response)
        if response['error'] is not None:
            raise JSONRPCException(response['error'])
        elif 'result' not in response:
            raise JSONRPCException({
                'code': -343, 'message': 'missing JSON-RPC result'})
        else:
            return response['result']

    def batch_(self, rpc_calls):
        """Batch RPC call.
           Pass array of arrays: [ [ "method", params... ], ... ]
           Returns array of results.
        """
        batch_data = []
        for rpc_call in rpc_calls:
            AuthServiceProxy.__id_count += 1
            m = rpc_call.pop(0)
            batch_data.append({"jsonrpc":"2.0", "method":m, "params":rpc_call, "id":AuthServiceProxy.__id_count})

        postdata = json.dumps(batch_data, default=EncodeDecimal)
        log.debug("--> "+postdata)
        http_response = self.__conn.urlopen('POST', self.__service_url, body=postdata, headers=self.__headers)

        results = []
        responses = self._parse_response(http_response)

        for response in responses:
            if response['error'] is not None:
                raise JSONRPCException(response['error'])
            elif 'result' not in response:
                raise JSONRPCException({
                    'code': -343, 'message': 'missing JSON-RPC result'})
            else:
                results.append(response['result'])
        return results

    def _parse_response(self, http_response):
        if not http_response.data:
            raise JSONRPCException({
                'code': -342, 'message': 'missing HTTP response from server'})

        response = json.loads(http_response.data, parse_float=decimal.Decimal)
        if "error" in response and response["error"] is None:
            log.debug("<-%s- %s"%(response["id"], json.dumps(response["result"], default=EncodeDecimal)))
        else:
            log.debug("<-- "+http_response.data)
        return response

