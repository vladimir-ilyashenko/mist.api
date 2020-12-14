import uuid
import json

# Python 2 and 3 support
from future.utils import string_types
from future.standard_library import install_aliases
install_aliases()
import urllib.request
import urllib.parse
import urllib.error

import mongoengine as me
from pyramid.response import Response

from mist.api import tasks

from mist.api.machines.models import Machine
from mist.api.scripts.models import Script, ExecutableScript
from mist.api.scripts.models import AnsibleScript

from mist.api.auth.methods import auth_context_from_request

from mist.api.exceptions import RequiredParameterMissingError
from mist.api.exceptions import BadRequestError, NotFoundError
from mist.api.exceptions import PolicyUnauthorizedError, UnauthorizedError

from mist.api.helpers import view_config, params_from_request
from mist.api.helpers import mac_sign

from mist.api.scripts.methods import filter_list_scripts

from mist.api.logs.methods import get_stories

from mist.api.tag.methods import add_tags_to_resource

from mist.api import config

OK = Response("OK", 200)


@view_config(route_name='api_v1_scripts', request_method='GET',
             renderer='json')
def list_scripts(request):
    """
    Tags: scripts
    ---
    Lists user scripts.
    READ permission required on each script.
    ---
    """
    auth_context = auth_context_from_request(request)
    scripts_list = filter_list_scripts(auth_context)
    return scripts_list


# SEC
@view_config(route_name='api_v1_scripts', request_method='POST',
             renderer='json')
def add_script(request):
    """
    Tags: scripts
    ---
    Add script to user scripts.
    ADD permission required on SCRIPT
    ---
    name:
      type: string
      required: true
    script:
      type: string
      required: false
    script_inline:
      type: string
      required: false
    script_github:
      type: string
      required: false
    script_url:
      type: string
      required: false
    location_type:
      type: string
      required: true
    entrypoint:
      type: string
    exec_type:
      type: string
      required: true
    description:
      type: string
    extra:
      type: object
    """

    params = params_from_request(request)

    # SEC
    auth_context = auth_context_from_request(request)
    script_tags, _ = auth_context.check_perm("script", "add", None)

    kwargs = {}

    for key in ('name', 'script', 'location_type', 'entrypoint',
                'exec_type', 'description', 'extra', 'script_inline',
                'script_url', 'script_github'
                ):
        kwargs[key] = params.get(key)   # TODO maybe change this

    kwargs['script'] = choose_script_from_params(kwargs['location_type'],
                                                 kwargs['script'],
                                                 kwargs['script_inline'],
                                                 kwargs['script_url'],
                                                 kwargs['script_github'])
    for key in ('script_inline', 'script_url', 'script_github'):
        kwargs.pop(key)

    name = kwargs.pop('name')
    exec_type = kwargs.pop('exec_type')

    if exec_type == 'executable':
        script = ExecutableScript.add(auth_context.owner, name, **kwargs)
    elif exec_type == 'ansible':
        script = AnsibleScript.add(auth_context.owner, name, **kwargs)
    else:
        raise BadRequestError(
            "Param 'exec_type' must be in ('executable', 'ansible')."
        )

    # Set ownership.
    script.assign_to(auth_context.user)

    if script_tags:
        add_tags_to_resource(auth_context.owner, script,
                             list(script_tags.items()))

    script = script.as_dict()

    if 'job_id' in params:
        script['job_id'] = params['job_id']

    return script


# TODO this isn't nice
def choose_script_from_params(location_type, script,
                              script_inline, script_url,
                              script_github):
    if script != '' and script is not None:
        return script

    if location_type == 'github':
        return script_github
    elif location_type == 'url':
        return script_url
    else:
        return script_inline


# SEC
@view_config(route_name='api_v1_script', request_method='GET', renderer='json')
def show_script(request):
    """
    Tags: scripts
    ---
    Show script details and job history.
    READ permission required on script.
    ---
    script_id:
      type: string
      required: true
      in: path
    """
    script_id = request.matchdict['script_id']
    auth_context = auth_context_from_request(request)

    if not script_id:
        raise RequiredParameterMissingError('No script id provided')

    try:
        script = Script.objects.get(owner=auth_context.owner,
                                    id=script_id, deleted=None)
    except me.DoesNotExist:
        raise NotFoundError('Script id not found')

    # SEC require READ permission on SCRIPT
    auth_context.check_perm('script', 'read', script_id)

    ret_dict = script.as_dict()
    jobs = get_stories('job', auth_context.owner.id, script_id=script_id)
    ret_dict['jobs'] = [job['job_id'] for job in jobs]
    return ret_dict


@view_config(route_name='api_v1_script_file', request_method='GET',
             renderer='json')
def download_script(request):
    """
    Tags: scripts
    ---
    Download script file or archive.
    READ permission required on script.
    ---
    script_id:
      type: string
      required: true
      in: path
    """
    script_id = request.matchdict['script_id']
    auth_context = auth_context_from_request(request)

    if not script_id:
        raise RequiredParameterMissingError('No script id provided')

    try:
        script = Script.objects.get(owner=auth_context.owner,
                                    id=script_id, deleted=None)
    except me.DoesNotExist:
        raise NotFoundError('Script id not found')

    # SEC require READ permission on SCRIPT
    auth_context.check_perm('script', 'read', script_id)
    try:
        return script.ctl.get_file()
    except BadRequestError():
        return Response("Unable to find: {}".format(request.path_info))


# SEC
@view_config(route_name='api_v1_script', request_method='DELETE',
             renderer='json')
def delete_script(request):
    """
    Tags: scripts
    ---
    Deletes script.
    REMOVE permission required on script.
    ---
    script_id:
      in: path
      required: true
      type: string
    """
    script_id = request.matchdict['script_id']
    auth_context = auth_context_from_request(request)

    if not script_id:
        raise RequiredParameterMissingError('No script id provided')

    try:
        script = Script.objects.get(owner=auth_context.owner, id=script_id,
                                    deleted=None)

    except me.DoesNotExist:
        raise NotFoundError('Script id not found')

    # SEC require REMOVE permission on script
    auth_context.check_perm('script', 'remove', script_id)

    script.ctl.delete()
    return OK


# SEC
@view_config(route_name='api_v1_scripts',
             request_method='DELETE', renderer='json')
def delete_scripts(request):
    """
    Tags: scripts
    ---
    Deletes multiple scripts.
    Provide a list of script ids to be deleted. The method will try to delete
    all of them and then return a json that describes for each script id
    whether or not it was deleted or the not_found if the script id could not
    be located. If no script id was found then a 404(Not Found) response will
    be returned.
    REMOVE permission required on each script.
    ---
    script_ids:
      required: true
      type: array
      items:
        type: string
    """
    auth_context = auth_context_from_request(request)
    params = params_from_request(request)
    script_ids = params.get('script_ids', [])
    if type(script_ids) != list or len(script_ids) == 0:
        raise RequiredParameterMissingError('No script ids provided')

    # remove duplicate ids if there are any
    script_ids = sorted(script_ids)
    i = 1
    while i < len(script_ids):
        if script_ids[i] == script_ids[i - 1]:
            script_ids = script_ids[:i] + script_ids[i + 1:]
        else:
            i += 1

    report = {}
    for script_id in script_ids:
        try:
            script = Script.objects.get(owner=auth_context.owner,
                                        id=script_id, deleted=None)
        except me.DoesNotExist:
            report[script_id] = 'not_found'
            continue
        # SEC require REMOVE permission on script
        try:
            auth_context.check_perm('script', 'remove', script_id)
        except PolicyUnauthorizedError:
            report[script_id] = 'unauthorized'
        else:
            script.ctl.delete()
            report[script_id] = 'deleted'
        # /SEC

    # if no script id was valid raise exception
    if len([script_id for script_id in report
            if report[script_id] == 'not_found']) == len(script_ids):
        raise NotFoundError('No valid script id provided')
    # if user was not authorized for any script raise exception
    if len([script_id for script_id in report
            if report[script_id] == 'unauthorized']) == len(script_ids):
        raise UnauthorizedError("You don't have authorization for any of these"
                                " scripts")
    return report


# SEC
@view_config(route_name='api_v1_script', request_method='PUT', renderer='json')
def edit_script(request):
    """
    Tags: scripts
    ---
    Edit script (rename only as for now).
    EDIT permission required on script.
    ---
    script_id:
      in: path
      required: true
      type: string
    new_name:
      type: string
      required: true
    new_description:
      type: string
    """
    script_id = request.matchdict['script_id']
    params = params_from_request(request)
    new_name = params.get('new_name')
    new_description = params.get('new_description')

    auth_context = auth_context_from_request(request)
    # SEC require EDIT permission on script
    auth_context.check_perm('script', 'edit', script_id)
    try:
        script = Script.objects.get(owner=auth_context.owner,
                                    id=script_id, deleted=None)
    except me.DoesNotExist:
        raise NotFoundError('Script id not found')

    if not new_name:
        raise RequiredParameterMissingError('No new name provided')

    script.ctl.edit(new_name, new_description)
    ret = {'new_name': new_name}
    if isinstance(new_description, string_types):
        ret['new_description'] = new_description
    return ret


# SEC
@view_config(route_name='api_v1_script', request_method='POST',
             renderer='json')
def run_script(request):
    """
    Tags: scripts
    ---
    Start a script job to run the script.
    READ permission required on cloud.
    RUN_SCRIPT permission required on machine.
    RUN permission required on script.
    ---
    script_id:
      in: path
      required: true
      type: string
    machine_uuid:
      required: true
      type: string
    params:
      type: string
    su:
      type: boolean
    env:
      type: string
    job_id:
      type: string
    """
    script_id = request.matchdict['script_id']
    params = params_from_request(request)
    script_params = params.get('params', '')
    su = params.get('su', False)
    env = params.get('env')
    job_id = params.get('job_id')
    if not job_id:
        job = 'run_script'
        job_id = uuid.uuid4().hex
    else:
        job = None
    if isinstance(env, dict):
        env = json.dumps(env)

    auth_context = auth_context_from_request(request)
    if 'machine_uuid' in params:
        machine_uuid = params.get('machine_uuid')
        if not machine_uuid:
            raise RequiredParameterMissingError('machine_uuid')

        try:
            machine = Machine.objects.get(id=machine_uuid,
                                          state__ne='terminated')
            # used by logging_view_decorator
            request.environ['machine_id'] = machine.id
            request.environ['cloud_id'] = machine.cloud.id
        except me.DoesNotExist:
            raise NotFoundError("Machine %s doesn't exist" % machine_uuid)
        cloud_id = machine.cloud.id
    else:
        # this will be depracated, keep it for backwards compatibility
        cloud_id = params.get('cloud_id')
        machine_id = params.get('machine_id')

        for key in ('cloud_id', 'machine_id'):
            if key not in params:
                raise RequiredParameterMissingError(key)
        try:
            machine = Machine.objects.get(cloud=cloud_id,
                                          external_id=machine_id,
                                          state__ne='terminated')
            # used by logging_view_decorator
            request.environ['machine_uuid'] = machine.id
        except me.DoesNotExist:
            raise NotFoundError("Machine %s doesn't exist" % machine_id)

    # SEC require permission READ on cloud
    auth_context.check_perm("cloud", "read", cloud_id)
    # SEC require permission RUN_SCRIPT on machine
    auth_context.check_perm("machine", "run_script", machine.id)
    # SEC require permission RUN on script
    auth_context.check_perm('script', 'run', script_id)
    try:
        script = Script.objects.get(owner=auth_context.owner,
                                    id=script_id, deleted=None)
    except me.DoesNotExist:
        raise NotFoundError('Script id not found')
    job_id = job_id or uuid.uuid4().hex
    tasks.run_script.delay(auth_context.owner.id, script.id,
                           machine.id, params=script_params,
                           env=env, su=su, job_id=job_id, job=job)
    return {'job_id': job_id, 'job': job}


@view_config(route_name='api_v1_script_url', request_method='GET',
             renderer='json')
def url_script(request):
    """
    Tags: scripts
    ---
    Returns to a mist authenticated user,
    a self-auth/signed url for fetching a script's file.
    READ permission required on script.
    ---
    script_id:
      in: path
      required: true
      type: string
    """
    auth_context = auth_context_from_request(request)
    script_id = request.matchdict['script_id']

    try:
        Script.objects.get(owner=auth_context.owner,
                           id=script_id, deleted=None)
    except Script.DoesNotExist:
        raise NotFoundError('Script does not exist.')

    # SEC require READ permission on script
    auth_context.check_perm('script', 'read', script_id)

    # build HMAC and inject into the `curl` command
    hmac_params = {'action': 'fetch_script', 'object_id': script_id}
    expires_in = 60 * 15
    mac_sign(hmac_params, expires_in)

    url = "%s/api/v1/fetch" % config.CORE_URI
    encode_params = urllib.parse.urlencode(hmac_params)
    r_url = url + '?' + encode_params

    return r_url


def fetch_script(script_id):
    """Used by mist.api.views.fetch"""
    try:
        script = Script.objects.get(id=script_id, deleted=None)
    except Script.DoesNotExist:
        raise NotFoundError('Script does not exist')
    return script.ctl.get_file()
