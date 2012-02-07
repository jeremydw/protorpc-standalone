#!/usr/bin/env python
#
# Copyright 2010 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import os
import StringIO
import types
import unittest
import urllib2

from google.appengine.api import apiproxy_stub_map
from google.appengine.api import urlfetch
from google.appengine.api import urlfetch_stub
from google.appengine.ext import testbed

from protorpc import messages
from protorpc import protobuf
from protorpc import protojson
from protorpc import remote
from protorpc import test_util
from protorpc import transport
from protorpc import webapp_test_util

import mox

package = 'transport_test'


def reset_urlfetch():
  """Configure urlfetch library on transport module."""
  transport.urlfetch = urlfetch
  transport.apiproxy_stub_map = apiproxy_stub_map


def clear_urlfetch():
  """Clear urlfetch module from transport library."""
  try:
    del transport.urlfetch
  except AttributeError:
    pass

  try:
    del transport.apiproxy_stub_map
  except AttributeError:
    pass


def setUp(self):
  # Always have consistent starting transport module.
  reset_urlfetch()


class ModuleInterfaceTest(test_util.ModuleInterfaceTest,
                          test_util.TestCase):

  MODULE = transport


class Message(messages.Message):

  value = messages.StringField(1)


class Service(remote.Service):

  @remote.method(Message, Message)
  def method(self, request):
    pass


# Remove when RPC is no longer subclasses.
class TestRpc(transport.Rpc):

  waited = False

  def _wait_impl(self):
    self.waited = True


class RpcTest(test_util.TestCase):

  def setUp(self):
    self.request = Message(value=u'request')
    self.response = Message(value=u'response')
    self.status = remote.RpcStatus(state=remote.RpcState.APPLICATION_ERROR,
                                   error_message='an error',
                                   error_name='blam')

    self.rpc = TestRpc(self.request)

  def testConstructor(self):
    self.assertEquals(self.request, self.rpc.request)
    self.assertEquals(remote.RpcState.RUNNING, self.rpc.state)
    self.assertEquals(None, self.rpc.error_message)
    self.assertEquals(None, self.rpc.error_name)

  def response(self):
    self.assertFalse(self.rpc.waited)
    self.assertEquals(None, self.rpc.response)
    self.assertTrue(self.rpc.waited)

  def testSetResponse(self):
    self.rpc.set_response(self.response)

    self.assertEquals(self.request, self.rpc.request)
    self.assertEquals(remote.RpcState.OK, self.rpc.state)
    self.assertEquals(self.response, self.rpc.response)
    self.assertEquals(None, self.rpc.error_message)
    self.assertEquals(None, self.rpc.error_name)

  def testSetResponseAlreadySet(self):
    self.rpc.set_response(self.response)

    self.assertRaisesWithRegexpMatch(
      transport.RpcStateError,
      'RPC must be in RUNNING state to change to OK',
      self.rpc.set_response,
      self.response)

  def testSetResponseAlreadyError(self):
    self.rpc.set_status(self.status)

    self.assertRaisesWithRegexpMatch(
      transport.RpcStateError,
      'RPC must be in RUNNING state to change to OK',
      self.rpc.set_response,
      self.response)

  def testSetStatus(self):
    self.rpc.set_status(self.status)

    self.assertEquals(self.request, self.rpc.request)
    self.assertEquals(remote.RpcState.APPLICATION_ERROR, self.rpc.state)
    self.assertEquals('an error', self.rpc.error_message)
    self.assertEquals('blam', self.rpc.error_name)
    self.assertRaisesWithRegexpMatch(remote.ApplicationError,
                                     'an error',
                                     getattr, self.rpc, 'response')

  def testSetStatusAlreadySet(self):
    self.rpc.set_response(self.response)

    self.assertRaisesWithRegexpMatch(
      transport.RpcStateError,
      'RPC must be in RUNNING state to change to OK',
      self.rpc.set_response,
      self.response)

  def testSetNonMessage(self):
    self.assertRaisesWithRegexpMatch(
      TypeError,
      'Expected Message type, received 10',
      self.rpc.set_response,
      10)

  def testSetStatusAlreadyError(self):
    self.rpc.set_status(self.status)

    self.assertRaisesWithRegexpMatch(
      transport.RpcStateError,
      'RPC must be in RUNNING state to change to OK',
      self.rpc.set_response,
      self.response)

  def testSetUninitializedStatus(self):
    self.assertRaises(messages.ValidationError,
                      self.rpc.set_status,
                      remote.RpcStatus())


class TransportTest(test_util.TestCase):

  def do_test(self, protocol, trans):
    request = Message()
    request.value = u'request'

    response = Message()
    response.value = u'response'

    encoded_request = protocol.encode_message(request)
    encoded_response = protocol.encode_message(response)

    self.assertEquals(protocol, trans.protocol)

    received_rpc = [None]
    def transport_rpc(remote, rpc_request):
      self.assertEquals(remote, Service.method.remote)
      self.assertEquals(request, rpc_request)
      rpc = TestRpc(request)
      rpc.set_response(response)
      return rpc
    trans._start_rpc = transport_rpc

    rpc = trans.send_rpc(Service.method.remote, request)
    self.assertEquals(response, rpc.response)

  def testDefaultProtocol(self):
    trans = transport.Transport()
    self.do_test(protobuf, trans)
    self.assertEquals(protobuf, trans.protocol_config.protocol)
    self.assertEquals('default', trans.protocol_config.name)

  def testAlternateProtocol(self):
    trans = transport.Transport(protocol=protojson)
    self.do_test(protojson, trans)
    self.assertEquals(protojson, trans.protocol_config.protocol)
    self.assertEquals('default', trans.protocol_config.name)

  def testProtocolConfig(self):
    protocol_config = remote.ProtocolConfig(
      protojson, 'protoconfig', 'image/png')
    trans = transport.Transport(protocol=protocol_config)
    self.do_test(protojson, trans)
    self.assertTrue(trans.protocol_config is protocol_config)


@remote.method(Message, Message)
def my_method(self, request):
  self.fail('self.my_method should not be directly invoked.')


class HttpTransportUrllibTest(test_util.TestCase):

  def setUp(self):
    super(HttpTransportUrllibTest, self).setUp()

    self.trans = transport.HttpTransport('http://myserver/myservice',
                                         protocol=protojson)

    self.request = Message(value=u'The request value')
    self.encoded_request = protojson.encode_message(self.request)

    self.response = Message(value=u'The response value')
    self.encoded_response = protojson.encode_message(self.response)

    self.mox = mox.Mox()
    self.mox.StubOutWithMock(urllib2, 'urlopen')

  def tearDown(self):
    super(HttpTransportUrllibTest, self).tearDown()

    self.mox.UnsetStubs()
    self.mox.VerifyAll()

  def VerifyRequest(self, urllib2_request):
    self.assertEquals('http://myserver/myservice.my_method',
                      urllib2_request.get_full_url())
    self.assertEquals(self.encoded_request,
                      urllib2_request.get_data())
    self.assertEquals('application/json',
                      urllib2_request.headers['Content-type'])

    return True

  def testCallSucceeds(self):
    urllib2.urlopen(mox.Func(self.VerifyRequest)).AndReturn(
        StringIO.StringIO(self.encoded_response))

    self.mox.ReplayAll()

    rpc = self.trans.send_rpc(my_method.remote, self.request)
    self.assertEquals(self.response, rpc.response)

  def testHttpError(self):
    urllib2.urlopen(mox.Func(self.VerifyRequest)).AndRaise(
      urllib2.HTTPError('http://whatever',
                        500,
                        'a server error',
                        {},
                        StringIO.StringIO('does not matter')))

    self.mox.ReplayAll()

    rpc = self.trans.send_rpc(my_method.remote, self.request)
    rpc.wait()
    self.assertEquals(remote.RpcState.SERVER_ERROR, rpc.state)
    self.assertEquals('HTTP Error 500: a server error',
                      rpc.error_message)
    self.assertEquals(None, rpc.error_name)

  def testErrorCheckedOnResultAttribute(self):
    urllib2.urlopen(mox.Func(self.VerifyRequest)).AndRaise(
      urllib2.HTTPError('http://whatever',
                        500,
                        'a server error',
                        {},
                        StringIO.StringIO('does not matter')))

    self.mox.ReplayAll()

    rpc = self.trans.send_rpc(my_method.remote, self.request)
    rpc.wait()
    self.assertRaisesWithRegexpMatch(remote.ServerError,
                                     'HTTP Error 500: a server error',
                                     getattr, rpc, 'response')

  def testErrorWithContent(self):
    status = remote.RpcStatus(state=remote.RpcState.REQUEST_ERROR,
                              error_message='an error')
    urllib2.urlopen(mox.Func(self.VerifyRequest)).AndRaise(
        urllib2.HTTPError('http://whatever',
                          500,
                          'An error occured',
                          {'content-type': 'application/json'},
                          StringIO.StringIO(protojson.encode_message(status))))

    self.mox.ReplayAll()

    rpc = self.trans.send_rpc(my_method.remote, self.request)
    rpc.wait()
    self.assertEquals(remote.RpcState.REQUEST_ERROR, rpc.state)
    self.assertEquals('an error', rpc.error_message)
    self.assertEquals(None, rpc.error_name)

  def testUnparsableErrorContent(self):
    urllib2.urlopen(mox.Func(self.VerifyRequest)).AndRaise(
        urllib2.HTTPError('http://whatever',
                          500,
                          'An error occured',
                          {'content-type': 'application/json'},
                          StringIO.StringIO('a text message is here anyway')))

    self.mox.ReplayAll()

    rpc = self.trans.send_rpc(my_method.remote, self.request)
    rpc.wait()
    self.assertEquals(remote.RpcState.SERVER_ERROR, rpc.state)
    self.assertEquals('HTTP Error 500: An error occured', rpc.error_message)
    self.assertEquals(None, rpc.error_name)

  def testURLError(self):
    trans = transport.HttpTransport('http://myserver/myservice',
                                    protocol=protojson)

    urllib2.urlopen(mox.IsA(urllib2.Request)).AndRaise(
      urllib2.URLError('a bad connection'))

    self.mox.ReplayAll()

    request = Message(value=u'The request value')
    rpc = trans.send_rpc(my_method.remote, request)
    rpc.wait()

    self.assertEquals(remote.RpcState.NETWORK_ERROR, rpc.state)
    self.assertEquals('Network Error: a bad connection', rpc.error_message)
    self.assertEquals(None, rpc.error_name)


class NoModuleHttpTransportUrllibTest(HttpTransportUrllibTest):

  def setUp(self):
    super(NoModuleHttpTransportUrllibTest, self).setUp()
    clear_urlfetch()

  def tearDown(self):
    super(NoModuleHttpTransportUrllibTest, self).tearDown()
    reset_urlfetch()


class URLFetchResponse(object):

  def __init__(self, content, status_code, headers):
    self.content = content
    self.status_code = status_code
    self.headers = headers


class HttpTransportUrlfetchTest(test_util.TestCase):

  def setUp(self):
    super(HttpTransportUrlfetchTest, self).setUp()

    # Need to initialize the urlfetch stub so that urlfetch detection works
    # properly.
    self.testbed = testbed.Testbed()
    self.testbed.activate()
    self.testbed.init_urlfetch_stub()

    transport.urlfetch = urlfetch
    transport.apiproxy_stub_map = apiproxy_stub_map

    self.trans = transport.HttpTransport('http://myserver/myservice',
                                         protocol=protojson)

    self.request = Message(value=u'The request value')
    self.encoded_request = protojson.encode_message(self.request)

    self.response = Message(value=u'The response value')
    self.encoded_response = protojson.encode_message(self.response)

    self.mox = mox.Mox()
    self.mox.StubOutWithMock(urlfetch, 'create_rpc')
    self.mox.StubOutWithMock(urlfetch, 'make_fetch_call')

    self.urlfetch_rpc = self.mox.CreateMockAnything()

  def tearDown(self):
    super(HttpTransportUrlfetchTest, self).tearDown()

    self.testbed.deactivate()

    self.mox.UnsetStubs()
    self.mox.VerifyAll()

  def ExpectRequest(self,
                    response_content=None,
                    response_code=200,
                    response_headers=None):
    urlfetch.create_rpc().AndReturn(self.urlfetch_rpc)
    urlfetch.make_fetch_call(self.urlfetch_rpc,
                             'http://myserver/myservice.my_method',
                             payload=self.encoded_request,
                             method='POST',
                             headers={'Content-type': 'application/json'})
    if response_content is None:
      response_content = self.encoded_response
    if response_headers is None:
      response_headers = {'content-type': 'application/json'}
    self.urlfetch_response = URLFetchResponse(response_content,
                                              response_code,
                                              response_headers)
    self.urlfetch_rpc.get_result().AndReturn(self.urlfetch_response)

  def testCallSucceeds(self):
    self.ExpectRequest()

    self.mox.ReplayAll()

    rpc = self.trans.send_rpc(my_method.remote, self.request)
    self.assertEquals(self.response, rpc.response)

  def testCallFails(self):
    self.ExpectRequest('an error', 500, {'content-type': 'text/plain'})

    self.mox.ReplayAll()

    rpc = self.trans.send_rpc(my_method.remote, self.request)
    rpc.wait()

    self.assertEquals(remote.RpcState.SERVER_ERROR, rpc.state)
    self.assertEquals('HTTP Error 500: Internal Server Error',
                      rpc.error_message)
    self.assertEquals(None, rpc.error_name)


class SimpleRequest(messages.Message):

  content = messages.StringField(1)


class SimpleResponse(messages.Message):

  content = messages.StringField(1)
  factory_value = messages.StringField(2)
  remote_host = messages.StringField(3)
  remote_address = messages.StringField(4)
  server_host = messages.StringField(5)
  server_port = messages.IntegerField(6)


class LocalService(remote.Service):

  def __init__(self, factory_value='default'):
    self.factory_value = factory_value

  @remote.method(SimpleRequest, SimpleResponse)
  def call_method(self, request):
    return SimpleResponse(content=request.content,
                          factory_value=self.factory_value,
                          remote_host=self.request_state.remote_host,
                          remote_address=self.request_state.remote_address,
                          server_host=self.request_state.server_host,
                          server_port=self.request_state.server_port)

  @remote.method()
  def raise_totally_unexpected(self, request):
    raise TypeError('Kablam')

  @remote.method()
  def raise_unexpected(self, request):
    raise remote.RequestError('Huh?')

  @remote.method()
  def raise_application_error(self, request):
    raise remote.ApplicationError('App error', 10)


class LocalTransportTest(test_util.TestCase):

  def CreateService(self, factory_value='default'):
    return 

  def testBasicCallWithClass(self):
    stub = LocalService.Stub(transport.LocalTransport(LocalService))
    response = stub.call_method(content='Hello')
    self.assertEquals(SimpleResponse(content='Hello',
                                     factory_value='default',
                                     remote_host=os.uname()[1],
                                     remote_address='127.0.0.1',
                                     server_host=os.uname()[1],
                                     server_port=-1),
                      response)

  def testBasicCallWithFactory(self):
    stub = LocalService.Stub(
      transport.LocalTransport(LocalService.new_factory('assigned')))
    response = stub.call_method(content='Hello')
    self.assertEquals(SimpleResponse(content='Hello',
                                     factory_value='assigned',
                                     remote_host=os.uname()[1],
                                     remote_address='127.0.0.1',
                                     server_host=os.uname()[1],
                                     server_port=-1),
                      response)

  def testTotallyUnexpectedError(self):
    stub = LocalService.Stub(transport.LocalTransport(LocalService))
    self.assertRaisesWithRegexpMatch(
      remote.ServerError,
      'Unexpected error TypeError: Kablam',
      stub.raise_totally_unexpected)

  def testUnexpectedError(self):
    stub = LocalService.Stub(transport.LocalTransport(LocalService))
    self.assertRaisesWithRegexpMatch(
      remote.ServerError,
      'Unexpected error RequestError: Huh?',
      stub.raise_unexpected)

  def testApplicationError(self):
    stub = LocalService.Stub(transport.LocalTransport(LocalService))
    self.assertRaisesWithRegexpMatch(
      remote.ApplicationError,
      'App error',
      stub.raise_application_error)


def main():
  unittest.main()


if __name__ == '__main__':
  main()
