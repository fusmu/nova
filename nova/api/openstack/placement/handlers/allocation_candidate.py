#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Placement API handlers for getting allocation candidates."""

import collections

from oslo_log import log as logging
from oslo_serialization import jsonutils
from oslo_utils import encodeutils
from oslo_utils import timeutils
import webob

from nova.api.openstack.placement import microversion
from nova.api.openstack.placement import util
from nova.api.openstack.placement import wsgi_wrapper
from nova import exception
from nova.i18n import _
from nova.objects import resource_provider as rp_obj


LOG = logging.getLogger(__name__)

# Represents the allowed query string parameters to the GET
# /allocation_candidates API call
_GET_SCHEMA_1_10 = {
    "type": "object",
    "properties": {
        "resources": {
            "type": "string"
        },
    },
    "required": [
        "resources",
    ],
    "additionalProperties": False,
}


def _transform_allocation_requests_dict(alloc_reqs):
    """Turn supplied list of AllocationRequest objects into a list of
    allocations dicts keyed by resource provider uuid of resources involved
    in the allocation request. The returned results are intended to be used
    as the body of a PUT /allocations/{consumer_uuid} HTTP request at
    micoversion 1.12 (and beyond). The JSON objects look like the following:

    [
        {
            "allocations": {
                $rp_uuid1: {
                    "resources": {
                        "MEMORY_MB": 512
                        ...
                    }
                },
                $rp_uuid2: {
                    "resources": {
                        "DISK_GB": 1024
                        ...
                    }
                }
            },
        },
        ...
    ]
    """
    results = []

    for ar in alloc_reqs:
        # A default dict of {$rp_uuid: "resources": {})
        rp_resources = collections.defaultdict(lambda: dict(resources={}))
        for rr in ar.resource_requests:
            res_dict = rp_resources[rr.resource_provider.uuid]['resources']
            res_dict[rr.resource_class] = rr.amount
        results.append(dict(allocations=rp_resources))

    return results


def _transform_allocation_requests_list(alloc_reqs):
    """Turn supplied list of AllocationRequest objects into a list of dicts of
    resources involved in the allocation request. The returned results is
    intended to be able to be used as the body of a PUT
    /allocations/{consumer_uuid} HTTP request, prior to microversion 1.12,
    so therefore we return a list of JSON objects that looks like the
    following:

    [
        {
            "allocations": [
                {
                    "resource_provider": {
                        "uuid": $rp_uuid,
                    }
                    "resources": {
                        $resource_class: $requested_amount, ...
                    },
                }, ...
            ],
        }, ...
    ]
    """
    results = []
    for ar in alloc_reqs:
        provider_resources = collections.defaultdict(dict)
        for rr in ar.resource_requests:
            res_dict = provider_resources[rr.resource_provider.uuid]
            res_dict[rr.resource_class] = rr.amount

        allocs = [
            {
                "resource_provider": {
                    "uuid": rp_uuid,
                },
                "resources": resources,
            } for rp_uuid, resources in provider_resources.items()
        ]
        alloc = {
            "allocations": allocs
        }
        results.append(alloc)
    return results


def _transform_provider_summaries(p_sums):
    """Turn supplied list of ProviderSummary objects into a dict, keyed by
    resource provider UUID, of dicts of provider and inventory information.

    {
       RP_UUID_1: {
           'resources': {
              'DISK_GB': {
                'capacity': 100,
                'used': 0,
              },
              'VCPU': {
                'capacity': 4,
                'used': 0,
              }
           }
       },
       RP_UUID_2: {
           'resources': {
              'DISK_GB': {
                'capacity': 100,
                'used': 0,
              },
              'VCPU': {
                'capacity': 4,
                'used': 0,
              }
           }
       }
    }
    """
    return {
        ps.resource_provider.uuid: {
            'resources': {
                psr.resource_class: {
                    'capacity': psr.capacity,
                    'used': psr.used,
                } for psr in ps.resources
            }
        } for ps in p_sums
    }


def _transform_allocation_candidates(alloc_cands, want_version):
    """Turn supplied AllocationCandidates object into a dict containing
    allocation requests and provider summaries.

    {
        'allocation_requests': <ALLOC_REQUESTS>,
        'provider_summaries': <PROVIDER_SUMMARIES>,
    }
    """
    if want_version.matches((1, 12)):
        a_reqs = _transform_allocation_requests_dict(
            alloc_cands.allocation_requests)
    else:
        a_reqs = _transform_allocation_requests_list(
            alloc_cands.allocation_requests)
    p_sums = _transform_provider_summaries(alloc_cands.provider_summaries)
    return {
        'allocation_requests': a_reqs,
        'provider_summaries': p_sums,
    }


@wsgi_wrapper.PlacementWsgify
@microversion.version_handler('1.10')
@util.check_accept('application/json')
def list_allocation_candidates(req):
    """GET a JSON object with a list of allocation requests and a JSON object
    of provider summary objects

    On success return a 200 and an application/json body representing
    a collection of allocation requests and provider summaries
    """
    context = req.environ['placement.context']
    want_version = req.environ[microversion.MICROVERSION_ENVIRON]
    schema = _GET_SCHEMA_1_10
    util.validate_query_params(req, schema)

    requests = util.parse_qs_request_groups(req.GET)

    try:
        cands = rp_obj.AllocationCandidates.get_by_requests(context, requests)
    except exception.ResourceClassNotFound as exc:
        raise webob.exc.HTTPBadRequest(
            _('Invalid resource class in resources parameter: %(error)s') %
            {'error': exc})

    response = req.response
    trx_cands = _transform_allocation_candidates(cands, want_version)
    json_data = jsonutils.dumps(trx_cands)
    response.body = encodeutils.to_utf8(json_data)
    response.content_type = 'application/json'
    if want_version.matches((1, 15)):
        response.cache_control = 'no-cache'
        response.last_modified = timeutils.utcnow(with_timezone=True)
    return response
