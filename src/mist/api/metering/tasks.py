import logging
import requests
import datetime

from mist.api import config
from mist.api.celery_app import app
from mist.api.machines.models import Machine
from mist.api.monitoring.methods import get_stats


log = logging.getLogger(__name__)


def _skip_metering(machine):
    # Prevents counting the vCPUs of docker containers and KVM guests.
    if machine.cloud.ctl.provider == 'docker':
        if machine.machine_type != 'container-host':
            return True
    if machine.cloud.ctl.provider == 'libvirt':
        if machine.extra.get('tags', {}).get('type') != 'hypervisor':
            return True
    return False


@app.task
def find_machine_cores():
    """Decide on the number of vCPUs for all machines"""

    def _get_cores_from_unix(machine):
        return machine.ssh_probe.cores if machine.ssh_probe else 0

    def _get_cores_from_tsdb(machine):
        if machine.monitoring.hasmonitoring:
            if machine.monitoring.method.endswith('graphite'):
                metric = 'cpu.*.idle'
            else:
                metric = 'cpu.cpu=/cpu\d/.usage_idle'
            return len(get_stats(machine, start='-60sec', metrics=[metric]))
        return 0

    def _get_cores_from_machine_extra(machine):
        try:
            return int(machine.extra.get('cpus', 0))
        except ValueError:
            return 0

    # def _get_cores_from_libcloud_size(machine):
    #     return machine.size.cpus if machine.size else 0

    for machine in Machine.objects(missing_since=None):
        try:
            machine.cores = (
                _get_cores_from_unix(machine) or
                _get_cores_from_tsdb(machine) or
                _get_cores_from_machine_extra(machine)  # or
                # _get_cores_from_libcloud_size(machine)
            )
            machine.save()
        except Exception as exc:
            log.error('Failed to get cores of machine %s: %r', machine.id, exc)


@app.task
def push_metering_info():
    """Collect and push new metering data to InfluxDB"""
    now = datetime.datetime.utcnow()
    metering = {}

    # Create database for storing metering data, if missing.
    db = requests.post(
        '%(host)s/query?q=CREATE DATABASE metering' % config.INFLUX
    )
    if not db.ok:
        raise Exception(db.content)

    # CPUs
    for machine in Machine.objects(last_seen__gte=now.date()):
        metering.setdefault(
            machine.owner.id,
            dict.fromkeys(('cores', 'checks', 'datapoints'), 0)
        )
        try:
            if _skip_metering(machine):
                continue
            metering[machine.owner.id]['cores'] += machine.cores or 0
        except Exception as exc:
            log.error('Failed upon cores metering of %s: %r', machine.id, exc)

    # TODO Checks
    # TODO Datapoints

    # Assemble points.
    points = []
    for owner, counters in metering.iteritems():
        value = ','.join(['%s=%s' % (k, v) for k, v in counters.iteritems()])
        point = 'usage,owner=%s %s' % (owner, value)
        points.append(point)

    # Write metering data.
    write = requests.post(
        '%(host)s/write?db=metering' % config.INFLUX, data='\n'.join(points)
    )
    if not write.ok:
        log.error('Failed to write metering data: %s', write.text)
