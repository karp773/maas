# Copyright 2015-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""API handlers: `Fabric`."""

from maasserver.api.support import (
    admin_method,
    OperationsHandler,
)
from maasserver.exceptions import MAASAPIValidationError
from maasserver.forms.fabric import FabricForm
from maasserver.models import Fabric
from maasserver.permissions import NodePermission
from maasserver.utils.orm import prefetch_queryset
from piston3.utils import rc


DISPLAYED_FABRIC_FIELDS = (
    'id',
    'name',
    'class_type',
    'vlans',
)


FABRIC_PREFETCH = [
    'vlan_set__primary_rack',
    'vlan_set__secondary_rack',
    'vlan_set__space',
    'vlan_set__relay_vlan__fabric__vlan_set',
    'vlan_set__relay_vlan__primary_rack',
    'vlan_set__relay_vlan__secondary_rack',
    'vlan_set__relay_vlan__space',
]


class FabricsHandler(OperationsHandler):
    """Manage fabrics."""
    api_doc_section_name = "Fabrics"
    update = delete = None
    fields = DISPLAYED_FABRIC_FIELDS

    @classmethod
    def resource_uri(cls, *args, **kwargs):
        # See the comment in NodeHandler.resource_uri.
        return ('fabrics_handler', [])

    def read(self, request):
        """List all fabrics."""
        fabrics = prefetch_queryset(Fabric.objects.all(), FABRIC_PREFETCH)
        # Preload the fabric on each vlan as that is already known, another
        # query is not required.
        for fabric in fabrics:
            for vlan in fabric.vlan_set.all():
                vlan.fabric = fabric
        return fabrics

    @admin_method
    def create(self, request):
        """Create a fabric.

        :param name: Name of the fabric.
        :param description: Description of the fabric.
        :param class_type: Class type of the fabric.
        """
        form = FabricForm(data=request.data)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)


class FabricHandler(OperationsHandler):
    """Manage fabric."""
    api_doc_section_name = "Fabric"
    create = None
    model = Fabric
    fields = DISPLAYED_FABRIC_FIELDS

    @classmethod
    def resource_uri(cls, fabric=None):
        # See the comment in NodeHandler.resource_uri.
        fabric_id = "id"
        if fabric is not None:
            fabric_id = fabric.id
        return ('fabric_handler', (fabric_id,))

    @classmethod
    def name(cls, fabric):
        """Return the name of the fabric."""
        return fabric.get_name()

    @classmethod
    def vlans(cls, fabric):
        """Return VLANs within the specified fabric."""
        return fabric.vlan_set.all()

    def read(self, request, id):
        """Read fabric.

        Returns 404 if the fabric is not found.
        """
        return Fabric.objects.get_fabric_or_404(
            id, request.user, NodePermission.view)

    def update(self, request, id):
        """Update fabric.

        :param name: Name of the fabric.
        :param description: Description of the fabric.
        :param class_type: Class type of the fabric.

        Returns 404 if the fabric is not found.
        """
        fabric = Fabric.objects.get_fabric_or_404(
            id, request.user, NodePermission.admin)
        form = FabricForm(instance=fabric, data=request.data)
        if form.is_valid():
            return form.save()
        else:
            raise MAASAPIValidationError(form.errors)

    def delete(self, request, id):
        """Delete fabric.

        Returns 404 if the fabric is not found.
        """
        fabric = Fabric.objects.get_fabric_or_404(
            id, request.user, NodePermission.admin)
        fabric.delete()
        return rc.DELETED
