# Copyright 2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for `maasserver.websockets.handlers.general`"""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

from maasserver.enum import (
    BOND_LACP_RATE_CHOICES,
    BOND_MODE_CHOICES,
    BOND_XMIT_HASH_POLICY_CHOICES,
    NODE_PERMISSION,
)
from maasserver.models.config import Config
from maasserver.node_action import ACTIONS_DICT
from maasserver.testing.factory import factory
from maasserver.testing.osystems import make_osystem_with_releases
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.websockets.handlers import general
from maasserver.websockets.handlers.general import GeneralHandler


class TestGeneralHandler(MAASServerTestCase):

    def dehydrate_actions(self, actions):
        return [
            {
                "name": name,
                "title": action.display,
                "sentence": action.display_sentence,
            }
            for name, action in actions.items()
            ]

    def test_architectures(self):
        arches = [
            "%s/%s" % (factory.make_name("arch"), factory.make_name("subarch"))
            for _ in range(3)
            ]
        for arch in arches:
            factory.make_usable_boot_resource(architecture=arch)
        handler = GeneralHandler(factory.make_User(), {})
        self.assertEquals(sorted(arches), handler.architectures({}))

    def test_osinfo(self):
        handler = GeneralHandler(factory.make_User(), {})
        osystem = make_osystem_with_releases(self)
        releases = [
            ("%s/%s" % (osystem["name"], release["name"]), release["title"])
            for release in osystem["releases"]
        ]
        expected_osinfo = {
            "osystems": [
                (osystem["name"], osystem["title"]),
            ],
            "releases": releases,
            "kernels": None,
            "default_osystem": Config.objects.get_config("default_osystem"),
            "default_release": Config.objects.get_config(
                "default_distro_series"),
        }
        self.assertItemsEqual(expected_osinfo, handler.osinfo({}))

    def test_node_actions_for_admin(self):
        handler = GeneralHandler(factory.make_admin(), {})
        actions_expected = self.dehydrate_actions(ACTIONS_DICT)
        self.assertItemsEqual(actions_expected, handler.node_actions({}))

    def test_node_actions_for_non_admin(self):
        handler = GeneralHandler(factory.make_User(), {})
        actions_expected = dict()
        for name, action in ACTIONS_DICT.items():
            permission = action.permission
            if action.installable_permission is not None:
                permission = action.installable_permission
            if permission != NODE_PERMISSION.ADMIN:
                actions_expected[name] = action
        actions_expected = self.dehydrate_actions(actions_expected)
        self.assertItemsEqual(actions_expected, handler.node_actions({}))

    def test_device_actions_for_non_admin(self):
        handler = GeneralHandler(factory.make_User(), {})
        actions_expected = self.dehydrate_actions({
            name: action
            for name, action in ACTIONS_DICT.items()
            if not action.installable_only
            })
        self.assertItemsEqual(actions_expected, handler.device_actions({}))

    def test_random_hostname_checks_hostname_existence(self):
        existing_node = factory.make_Node(hostname="hostname")
        hostnames = [existing_node.hostname, "new-hostname"]
        self.patch(
            general, "gen_candidate_names",
            lambda: iter(hostnames))
        handler = GeneralHandler(factory.make_User(), {})
        self.assertEqual("new-hostname", handler.random_hostname({}))

    def test_random_hostname_returns_empty_string_if_all_used(self):
        existing_node = factory.make_Node(hostname='hostname')
        hostnames = [existing_node.hostname]
        self.patch(
            general, "gen_candidate_names",
            lambda: iter(hostnames))
        handler = GeneralHandler(factory.make_User(), {})
        self.assertEqual("", handler.random_hostname({}))

    def test_bond_options(self):
        handler = GeneralHandler(factory.make_User(), {})
        self.assertEquals({
            "modes": BOND_MODE_CHOICES,
            "lacp_rates": BOND_LACP_RATE_CHOICES,
            "xmit_hash_policies": BOND_XMIT_HASH_POLICY_CHOICES,
            }, handler.bond_options({}))
