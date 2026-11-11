"""Opt-in timings. Never log query documents, URLs, identities or request bodies."""
import logging
import os
import random
from contextlib import contextmanager
from time import perf_counter

from flask import g, has_request_context, request, before_render_template, template_rendered
from pymongo.monitoring import CommandListener

log = logging.getLogger('smartliving.performance')


def sample_rate():
    try:
        return min(1.0, max(0.0, float(os.getenv('PERFORMANCE_SAMPLE_RATE', '0'))))
    except ValueError:
        return 0.0


@contextmanager
def timed_operation(name):
    start = perf_counter()
    try:
        yield
    finally:
        if has_request_context() and getattr(g, '_perf_sample', False):
            log.info('operation=%s duration_ms=%.3f', name, (perf_counter() - start) * 1000)


class DatabaseTimings(CommandListener):
    def started(self, event):
        if has_request_context() and getattr(g, '_perf_sample', False):
            g._perf_db_count += 1

    def succeeded(self, event):
        self._finished(event, 'ok')

    def failed(self, event):
        self._finished(event, 'failed')

    def _finished(self, event, outcome):
        if has_request_context() and getattr(g, '_perf_sample', False):
            g._perf_db_ms += event.duration_micros / 1000
            log.info('database_command=%s outcome=%s duration_ms=%.3f',
                     event.command_name, outcome, event.duration_micros / 1000)


def install(app):
    @app.before_request
    def start():
        g._perf_sample = random.random() < sample_rate()
        g._perf_start = perf_counter()
        g._perf_db_count = 0
        g._perf_db_ms = 0.0

    def template_start(sender, **kwargs):
        g._perf_template_start = perf_counter()

    def template_end(sender, **kwargs):
        if getattr(g, '_perf_sample', False):
            log.info('operation=template duration_ms=%.3f',
                     (perf_counter() - g._perf_template_start) * 1000)

    before_render_template.connect(template_start, app, weak=False)
    template_rendered.connect(template_end, app, weak=False)

    @app.after_request
    def finish(response):
        if getattr(g, '_perf_sample', False):
            log.info('endpoint=%s method=%s status=%s duration_ms=%.3f bytes=%s db_commands=%s db_ms=%.3f',
                     request.endpoint or 'unmatched', request.method, response.status_code,
                     (perf_counter() - g._perf_start) * 1000, response.content_length,
                     g._perf_db_count, g._perf_db_ms)
        return response
