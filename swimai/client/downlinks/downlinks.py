#  Copyright 2015-2019 SWIM.AI inc.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import asyncio
import inspect

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from swimai.client.utils import URI
from swimai.structures import Absent
from swimai.warp import SyncRequest, CommandMessage, Envelope

# Imports for type annotations
if TYPE_CHECKING:
    from swimai import SwimClient


class ValueDownlinkModel:

    def __init__(self, client: 'SwimClient') -> None:
        self.client = client
        self.host_uri = None
        self.node_uri = None
        self.lane_uri = None
        self.connection = None
        self.task = None
        self.downlink = None
        self.value = None

        self.linked = asyncio.Event(loop=self.client.loop)
        self.synced = asyncio.Event(loop=self.client.loop)

    def open(self) -> 'ValueDownlinkModel':
        self.task = self.client.schedule_task(self.connection.wait_for_messages)
        return self

    def close(self) -> 'ValueDownlinkModel':
        self.client.schedule_task(self.__close)
        return self

    async def establish_downlink(self) -> None:
        """
        Send a `sync` request in order to start getting messages from the remote agent.
        """
        sync_request = SyncRequest(self.node_uri, self.lane_uri)
        await self.connection.send_message(await sync_request.to_recon())

    async def receive_message(self, message: 'Envelope') -> None:
        """
        Handle a message from the remote agent.

        :param message:         - Message received from the remote agent.
        """
        if message.tag == 'linked':
            self.linked.set()
        elif message.tag == 'synced':
            self.synced.set()
        elif message.tag == 'event':
            await self.__set_value(message)

    def get(self, wait_sync: bool = False) -> Any:
        """
        Return the value of the downlink.

        :param wait_sync:       - If True, wait for the initial sync to be completed before returning.
                                  If False, return immediately.
        :return:                - The value of the Downlink.
        """
        if wait_sync:
            task = self.client.schedule_task(self.__get_value)
            return task.result()
        else:
            return self.value

    async def send_message(self, message: 'Envelope') -> None:
        """
        Send a message to the remote agent of the downlink.

        :param message:         - Message to send to the remote agent.
        """
        await self.synced.wait()
        await self.connection.send_message(await message.to_recon())

    async def __get_value(self) -> Any:
        """
        Get the value of the downlink after it has been synced.

        :return:                - The current value of the downlink.
        """
        await self.synced.wait()
        return self.value

    async def __set_value(self, message: 'Envelope') -> None:
        """
        Set the value of the the downlink and trigger the `did_set` callback of the relevant subscribers.

        :param message:        - The message from the remote agent.
        :return:
        """
        old_value = self.value

        if message.body == Absent.get_absent():
            self.value = None
        else:
            self.value = message.body.value

        await self.downlink.subscribers_did_set(self.value, old_value)

    async def __close(self):
        self.task.cancel()


class ValueDownlinkView:

    def __init__(self, client: 'SwimClient') -> None:
        self.client = client
        self.host_uri = None
        self.node_uri = None
        self.lane_uri = None
        self.did_set_callback = None

        self.is_open = False
        self.initialised = asyncio.Event(loop=self.client.loop)
        self.model = None
        self.connection = None

    @property
    def route(self) -> str:
        return f'{self.node_uri}/{self.lane_uri}'

    def open(self) -> 'ValueDownlinkView':

        if not self.is_open:
            self.is_open = True
            self.client.schedule_task(self.client.add_downlink_view, self)

        return self

    def close(self) -> 'ValueDownlinkView':

        if self.is_open:
            self.is_open = False
            self.client.schedule_task(self.client.remove_downlink_view, self)

        return self

    async def establish_downlink(self) -> None:
        await self.model.establish_downlink()

    async def create_downlink_model(self) -> 'ValueDownlinkModel':
        model = ValueDownlinkModel(self.client)
        model.host_uri = self.host_uri
        model.node_uri = self.node_uri
        model.lane_uri = self.lane_uri

        return model

    def set_host_uri(self, host_uri: str) -> 'ValueDownlinkView':
        self.host_uri = URI.normalise_warp_scheme(host_uri)
        return self

    def set_node_uri(self, node_uri: str) -> 'ValueDownlinkView':
        self.node_uri = node_uri
        return self

    def set_lane_uri(self, lane_uri: str) -> 'ValueDownlinkView':
        self.lane_uri = lane_uri
        return self

    def did_set(self, function: Callable) -> 'ValueDownlinkView':

        if inspect.iscoroutinefunction(function) or isinstance(function, Callable):
            self.did_set_callback = function
        else:
            raise TypeError('Callback must be a coroutine or function!')

        return self

    # noinspection PyAsyncCall
    async def execute_did_set(self, current_value: Any, old_value: Any) -> None:
        if self.did_set_callback:
            self.client.schedule_task(self.did_set_callback, current_value, old_value)

    def set(self, value: Any) -> None:
        if self.is_open:
            message = CommandMessage(self.node_uri, self.lane_uri, value)
            self.client.schedule_task(self.send_message, message)
        else:
            raise RuntimeError('Link is not open!')

    async def send_message(self, message: Envelope) -> None:
        await self.initialised.wait()
        await self.model.send_message(message)

    def get(self, wait_sync: bool = False) -> Any:
        if self.is_open:
            return self.model.get(wait_sync)
        else:
            raise RuntimeError('Link is not open!')
