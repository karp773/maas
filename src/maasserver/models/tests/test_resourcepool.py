# Copyright 2013-2017 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test ResourcePool objects."""

from django.core.exceptions import ValidationError
from maasserver.models.resourcepool import (
    DEFAULT_RESOURCEPOOL_DESCRIPTION,
    DEFAULT_RESOURCEPOOL_NAME,
    ResourcePool,
)
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils.orm import reload_object


class TestResourcePoolManager(MAASServerTestCase):
    """Tests for `ResourcePool` manager."""

    def test_get_default_resource_pool_returns_default_pool(self):
        pool = ResourcePool.objects.get_default_resource_pool()
        self.assertEqual(pool.id, 0)
        self.assertEqual(pool.name, DEFAULT_RESOURCEPOOL_NAME)
        self.assertEqual(pool.description, DEFAULT_RESOURCEPOOL_DESCRIPTION)
        self.assertIsNotNone(pool.created)
        self.assertIsNotNone(pool.updated)

    def test_get_default_resource_pool_ignores_other_pools(self):
        factory.make_ResourcePool()
        self.assertEqual(
            ResourcePool.objects.get_default_resource_pool().name,
            DEFAULT_RESOURCEPOOL_NAME)


class TestResourcePool(MAASServerTestCase):

    def test_init(self):
        name = factory.make_name('name')
        description = factory.make_name('description')
        pool = ResourcePool(name=name, description=description)
        pool.save()
        pool = reload_object(pool)
        self.assertEqual(pool.name, name)
        self.assertEqual(pool.description, description)

    def test_is_default_true(self):
        self.assertTrue(
            ResourcePool.objects.get_default_resource_pool().is_default())

    def test_is_default_false(self):
        self.assertFalse(factory.make_ResourcePool().is_default())

    def test_delete(self):
        pool = factory.make_ResourcePool()
        pool.delete()
        self.assertIsNone(reload_object(pool))

    def test_delete_default_fails(self):
        pool = ResourcePool.objects.get_default_resource_pool()
        self.assertRaises(ValidationError, pool.delete)
