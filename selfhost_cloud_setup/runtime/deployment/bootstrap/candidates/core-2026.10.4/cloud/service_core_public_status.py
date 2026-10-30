"""Bounded public updater snapshot shared by its producer and the HA display.

Snapshots describe stored evidence, never live HA health or permission to execute.
No network, credentials, subprocesses or runtime changes occur in this module.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat

MAX_BYTES = 8192
STATES = frozenset(('checking', 'qualified', 'waiting', 'confirmed', 'recovered', 'needs_diagnosis', 'unavailable'))


def _time(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Bounded UTC time required')
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('UTC time required')
    return parsed


def validate(value, *, now=None):
    """Return a minimal immutable-by-copy view; reject ambiguous or stale input."""
    if (not isinstance(value, dict) or set(value) != {'schema', 'scope', 'generated_at', 'candidate', 'maintenance'}
            or type(value['schema']) is not int or value['schema'] != 1
            or value['scope'] != 'stored_updater_records_only'):
        raise ValueError('Exact public updater snapshot required')
    age = ((now or datetime.now(timezone.utc)) - _time(value['generated_at'])).total_seconds()
    if not 0 <= age <= 900:
        raise ValueError('Fresh public updater snapshot required')
    result = {'schema': 1, 'scope': value['scope'], 'generated_at': value['generated_at']}
    for key in ('candidate', 'maintenance'):
        item = value[key]
        if (not isinstance(item, dict) or set(item) - {'state', 'core_version', 'record_updated_at'}
                or not isinstance(item.get('state'), str) or item['state'] not in STATES):
            raise ValueError('Exact public updater state required')
        cleaned = {'state': item['state']}
        if 'core_version' in item:
            version = item['core_version']
            if not isinstance(version, str) or not re.fullmatch(r'20[0-9]{2}\.[0-9]{1,2}\.[0-9]+', version):
                raise ValueError('Stable public Core version required')
            cleaned['core_version'] = version
        if 'record_updated_at' in item:
            stamp = _time(item['record_updated_at'])
            if stamp > _time(value['generated_at']):
                raise ValueError('Record time cannot follow export time')
            cleaned['record_updated_at'] = item['record_updated_at']
        result[key] = cleaned
    return result


def decode(raw, *, now=None):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError('Bounded public snapshot bytes required')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate snapshot field')
            result[key] = value
        return result
    try:
        return validate(json.loads(raw, object_pairs_hook=unique), now=now)
    except (UnicodeError, RecursionError) as error:
        raise ValueError('Invalid public snapshot encoding') from error


def read_file(path, *, now=None):
    """Read one regular snapshot without following its final directory or file."""
    directory = descriptor = None
    try:
        path = Path(path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_BYTES:
            raise ValueError('Regular bounded snapshot required')
        raw = os.read(descriptor, MAX_BYTES + 1)
        return {'available': True, 'snapshot': decode(raw, now=now)}
    except (OSError, ValueError, TypeError, OverflowError):
        return {'available': False}
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


def from_records(records, *, now=None):
    """Convert the existing allowlisted reader output; drop every extra field."""
    clock = now or datetime.now(timezone.utc)
    result = {'schema': 1, 'scope': 'stored_updater_records_only', 'generated_at': clock.isoformat()}
    for source, target in (('pipeline', 'candidate'), ('maintenance', 'maintenance')):
        record = records.get(source) or {}
        status = record.get('reported_status')
        if record.get('record_state') != 'available':
            state = 'unavailable'
        elif status == 'rolled_back_health_confirmed':
            state = 'recovered'
        elif status == 'applied_health_confirmed':
            state = 'confirmed'
        elif status in ('ready_for_controlled_code_update',):
            state = 'qualified'
        elif status in ('preparing', 'staging', 'execution_attempted', 'rollback_staging', 'rollback_execution_attempted'):
            state = 'checking'
        elif status in ('maintenance_safety_wait', 'operating_preflight_blocked', 'awaiting_health', 'staged_safety_wait',
                         'awaiting_qualification', 'awaiting_approved_code_baseline', 'no_detected_update'):
            state = 'waiting'
        elif status in ('awaiting_native_image', 'awaiting_native_export', 'awaiting_matching_native_export',
                        'awaiting_fresh_operating_facts', 'awaiting_operating_machine_alignment',
                        'awaiting_target_image_profile', 'awaiting_functional_qualification',
                        'awaiting_native_contract_evidence', 'awaiting_baseline_ui_qualification',
                        'awaiting_operating_preflight', 'rollback_safety_wait', 'busy'):
            state = 'waiting'
        else:
            state = 'needs_diagnosis'
        item = {'state': state}
        version = record.get('core_version')
        if isinstance(version, str) and re.fullmatch(r'20[0-9]{2}\.[0-9]{1,2}\.[0-9]+', version):
            item['core_version'] = version
        stamp = record.get('file_updated_at_utc')
        if isinstance(stamp, str):
            try:
                if _time(stamp) <= clock:
                    item['record_updated_at'] = stamp
            except ValueError:
                pass
        result[target] = item
    return validate(result, now=clock)
