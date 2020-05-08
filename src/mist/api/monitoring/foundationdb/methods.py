import asyncio
import logging
import requests
import time
import urllib.parse

from mist.api.exceptions import ForbiddenError
from mist.api.exceptions import ServiceUnavailableError
from mist.api import config


log = logging.getLogger(__name__)


def get_stats(machine, start="", stop="", step="", metrics=None):
    data = {}

    if isinstance(machine, str):
        machine_id = machine
    else:
        machine_id = machine.id

    # If no metrics are specified, then we get all of them
    if not metrics:
        metrics = [('fetch(\"{id}.*\"' +
                    ', start=\"{start}\", stop=\"{stop}\"' +
                    ', step=\"{step}\")')]

    for metric in metrics:
        # processed_metric = "%s.%s" % (machine.id, metric)
        query = metric.format(id=machine_id, start=start, stop=stop, step=step)
        """query = 'fetch("%s", start="%s", stop="%s", step="%s")' % (
            processed_metric,
            start,
            stop,
            step,
        )"""
        try:
            raw_machine_data = requests.get(
                "%s/v1/datapoints?query=%s"
                % (config.TSFDB_URI, urllib.parse.quote(query)),
                timeout=20
            )
        except Exception as exc:
            log.error(
                'Got %r on get_stats for resource %s'
                % (exc, machine_id))
            raise ServiceUnavailableError()

        if not raw_machine_data.ok:
            log.error('Got %d on get_stats: %s',
                      raw_machine_data.status_code, raw_machine_data.content)
            raise ServiceUnavailableError()

        raw_machine_data = raw_machine_data.json()
        raw_metrics = list(raw_machine_data.get("series", {}).keys())
        for raw_metric in raw_metrics:
            # We use as key the metric name without the machine id
            # e.g "id.system.load1 => system.load1"
            returned_metric = raw_metric.split(".", 1)[1]
            data.update(
                {
                    returned_metric: {
                        "name": returned_metric,
                        "datapoints": raw_machine_data["series"].get(
                            raw_metric, []),
                    }
                }
            )

    if not isinstance(machine, str):
        # set activated_at for collectd/telegraf installation status
        # if no data previously received for machine
        from mist.api.helpers import trigger_session_update
        from mist.api.rules.tasks import add_nodata_rule

        istatus = machine.monitoring.installation_status
        if not istatus.activated_at:
            for val in (point[0] for item in list(data.values())
                        for point in item['datapoints']
                        if point[1] >= istatus.started_at):
                if val is not None:
                    if not istatus.finished_at:
                        istatus.finished_at = time.time()
                    istatus.activated_at = time.time()
                    istatus.state = 'succeeded'
                    machine.save()
                    add_nodata_rule.delay(machine.owner.id)
                    trigger_session_update(machine.owner, ['monitoring'])
                    break

    return data


def _get_load_machine(machine, start, stop, step):
    metric = "%s.system.load1" % machine
    query = ('roundY(fetch("%s", start="%s", stop="%s", step="%s")' +
             ', base=5)') % \
        (
        metric,
        start,
        stop,
        step,
    )
    try:
        raw_machine_data = requests.get(
            "%s/v1/datapoints?query=%s" % (config.TSFDB_URI, query),
            timeout=5
        )
    except Exception as exc:
        log.error(
            'Got %r on _get_load_machine for resource %s'
            % (exc, machine))
        return {}

    if not raw_machine_data.ok:
        log.error('Got %d on _get_load_machine: %s',
                  raw_machine_data.status_code, raw_machine_data.content)
        return {}

    raw_machine_data = raw_machine_data.json()

    return(
        {
            machine: {
                "name": machine,
                "datapoints": raw_machine_data["series"].get(metric, []),
            }
        }
    )


async def _get_load(machines, start, stop, step):
    loop = asyncio.get_event_loop()
    loads = [
        loop.run_in_executor(None, _get_load_machine, *
                             (machine, start, stop, step))
        for machine in machines
    ]
    return await asyncio.gather(*loads)


def get_load(machines, start, stop, step):
    loop = asyncio.get_event_loop()
    loads = loop.run_until_complete(_get_load(machines, start, stop, step))
    data = {}
    for load in loads:
        data.update(load)

    return data


def find_metrics(machine):
    if not machine.monitoring.hasmonitoring:
        raise ForbiddenError("Machine doesn't have monitoring enabled.")
    try:
        data = requests.get("%s/v1/resources/%s" %
                            (config.TSFDB_URI, machine.id),
                            timeout=5)
    except Exception as exc:
        log.error(
            'Got %r on find_metrics for resource %s'
            % (exc, machine.id))
        raise ServiceUnavailableError()

    if not data.ok:
        log.error('Got %d on find_metrics: %s',
                  data.status_code, data.content)
        raise ServiceUnavailableError()

    return data.json()