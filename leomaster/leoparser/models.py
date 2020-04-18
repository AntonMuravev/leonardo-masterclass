import re
import json
import uuid
import lxml
import lxml.html
from django.db import models
from django.utils import timezone
from dateutil.relativedelta import *
from dictdiffer import diff, patch, revert
from django.contrib.postgres.fields import JSONField
from django.core.exceptions import ObjectDoesNotExist


class Parser(models.Model):
    name = models.TextField(verbose_name='name', unique=True, blank=False)
    rule_set = models.ManyToManyField('Rule', verbose_name='rule', related_name='parsers')

    def __init__(self, *args, **kwargs):
        super(Parser, self).__init__(*args, **kwargs)
        self._rules = {}

    @property
    def rules(self):
        if self.id:
            if not self._rules:
                self._rules = {rule.id: rule for rule in self.rule_set.select_related('typeof', 'parent').all()}
                self._init_children()
        return self._rules

    def _init_children(self):
        for _id, rule in self.rules.items():
            rule.set_children([r for r in self.rules.values() if r.parent_id == _id])

    def _get_roots(self):
        return [rule for rule in self.rules.values() if rule.parent is None]

    def _go_through_rules(self, root, html, doc, level=1):
        try:
            result = root.apply(html)
        except Exception:
            result = '__error__'
        if isinstance(result, (list, tuple)):
            for index, peace_of_result in enumerate(result):
                if isinstance(peace_of_result, lxml.html.HtmlElement):
                    context_doc = dict()
                    doc[root.name + '_' + str(index)] = context_doc
                    context_html = peace_of_result
                else:
                    doc.setdefault(root.name, dict()).update({index: peace_of_result})
                    context_doc = doc[root.name]
                    context_html = html
                for rule in root.children:
                    print(' ' * (level * 3) + '|_', str(rule))
                    self._go_through_rules(rule, context_html, context_doc, level=level + 1)
        else:
            if isinstance(result, lxml.html.HtmlElement):
                context_doc = dict()
                doc[root.name] = context_doc
                context_html = result
            else:
                doc[root.name] = result
                context_doc = doc
                context_html = html
            for rule in root.children:
                print(' ' * (level * 3) + '|_', str(rule))
                self._go_through_rules(rule, context_html, context_doc, level=level + 1)

    def go_through(self, html, doc):
        root_rules = self._get_roots()
        for root in root_rules:
            print('|_', str(root))
            self._go_through_rules(root, html, doc)

    def parse(self, content):
        doc = dict()
        html = lxml.html.document_fromstring(content)
        self.go_through(html, doc)
        return doc


class Rule(models.Model):
    name = models.TextField(verbose_name='name', blank=False)
    xpath = models.TextField(verbose_name='xpath', blank=False)
    regex = models.TextField(verbose_name='regex', blank=True)
    sub = models.TextField(verbose_name='sub', blank=True)
    typeof = models.ForeignKey('TypeOf', on_delete=models.CASCADE)
    parent = models.ForeignKey('Rule', related_name='children_set', null=True, blank=True, on_delete=models.CASCADE)

    def set_children(self, children):
        self.children = children

    class Meta:
        ordering = ('name', )
        unique_together = ('name', 'parent',)

    def apply(self, element):
        res = element.xpath(self.xpath)
        if self.regex:
            regex = re.compile(self.regex, re.UNICODE | re.IGNORECASE)
            extracted = regex.search(res)
            res = extracted.group(0) if extracted else res
            if self.sub:
                res = regex.sub(self.sub, res)
        return self.typeof.convert(res)

    def __str__(self):
        return '%s::%s(%s)' % (self.typeof, self.name, self.xpath)

    def __repr__(self):
        return '<%s: id="%s" body="%s">' % (self.__class__.__name__, self.id, self.__str__(),)

    def to_dict(self):
        return {'id': self.id,
                'name': self.name,
                'xpath': self.xpath,
                'type': self.typeof.name,
                'parent': self.parent.id if self.parent else None,
                'children': [c.id for c in self.children.all()]}


def apply_once_or_many(f):
    def wrapper(value):
        if isinstance(value, (list, tuple)):
            if len(value) > 1:
                return [f(v) for v in value]
            elif len(value) == 1:
                value = value[0]
        return f(value)
    return wrapper


class TypeOf(models.Model):
    T_CONTAINER = 'container'
    T_CURRENCY = 'currency'
    T_DATE = 'date'
    T_DATETIME = 'datetime'
    T_FLOAT = 'float'
    T_INTEGER = 'integer'
    T_STRING = 'string'
    T_TIME = 'time'

    options = [
        (T_CONTAINER, T_CONTAINER),
        (T_CURRENCY, T_CURRENCY),
        (T_DATE, T_DATE),
        (T_DATETIME, T_DATETIME),
        (T_FLOAT, T_FLOAT),
        (T_INTEGER, T_INTEGER),
        (T_STRING, T_STRING),
        (T_TIME, T_TIME),
    ]

    name = models.TextField(verbose_name='name', choices=options, unique=True, blank=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.converters = {
            self.T_CONTAINER: self.to_container,
            self.T_CURRENCY: self.to_currency,
            self.T_DATE: self.to_date,
            self.T_DATETIME: self.to_datetime,
            self.T_FLOAT: self.to_float,
            self.T_INTEGER: self.to_integer,
            self.T_STRING: self.to_string,
            self.T_TIME: self.to_time,
        }

    class Meta:
        ordering = ('name',)

    def convert(self, value):
        return self.converters.get(self.name, lambda v: v)(value)

    @staticmethod
    @apply_once_or_many
    def to_container(value):
        return value

    @staticmethod
    @apply_once_or_many
    def to_currency(value):
        return value

    @staticmethod
    @apply_once_or_many
    def to_date(value):
        return value

    @staticmethod
    @apply_once_or_many
    def to_datetime(value):
        return value

    @staticmethod
    @apply_once_or_many
    def to_float(value):
        return float(value)

    @staticmethod
    @apply_once_or_many
    def to_integer(value):
        return int(value)

    @staticmethod
    @apply_once_or_many
    def to_string(value):
        return str(value).strip()

    @staticmethod
    @apply_once_or_many
    def to_time(value):
        return value

    def __str__(self):
        return self.name

    def __repr__(self):
        return '<%s: id="%s" name="%s">' % (self.__class__.__name__, self.id, self.__str__(),)


class DocDelta(models.Model):
    base = models.ForeignKey('GenericDocument', related_name='delta_set', null=False, on_delete=models.CASCADE)
    created = models.DateTimeField(auto_now_add=True)
    delta = JSONField(default=list)

    class Meta:
        ordering = ('-created',)

    def __str__(self):
        return json.dumps(self.delta, indent=1)

    def __repr__(self):
        return '<%s: id="%s" delta="%s">' % (self.__class__.__name__, self.id, self.delta,)


class HistoryManager(models.Manager):

    def __init__(self, unique_field='uid', *args, **kwargs):
        self.unique_field = unique_field
        super().__init__(*args, **kwargs)

    def save(self, content, *args, **kwargs):
        try:
            unique_value = str(content.pop(self.unique_field))
        except KeyError:
            unique_value = uuid.uuid4().hex

        lookup = {self.unique_field: unique_value}
        try:

            doc = self.get_queryset().get(**lookup)
            doc.content = content
            delta = doc.delta
            doc.save(*args, **kwargs)
            is_new = False
        except ObjectDoesNotExist:
            doc = self.get_queryset().create(content=content, **lookup)
            delta = doc.delta
            is_new = True

        return doc, is_new, delta


class GenericDocument(models.Model):
    uid = models.TextField(unique=True)
    content = JSONField(default=dict, null=False)
    created = models.DateTimeField(auto_now_add=True)
    modified = models.DateTimeField(auto_now=True)
    _track_change = False
    _track_add = False
    _track_remove = False

    objects = models.Manager()
    history = HistoryManager()

    def __init__(self, *args, **kwargs):
        self._old_content = None
        super().__init__(*args, **kwargs)

    @property
    def actions(self):
        actions = []
        if self._track_change:
            actions.append('change')
        if self._track_add:
            actions.append('add')
        if self._track_remove:
            actions.append('remove')
        return actions

    def __setattr__(self, key, value):
        if key == 'content':
            # instance has already created
            if self.__dict__.get('id') and self._old_content is None:
                self._old_content = self.__dict__.get('content')
        return super().__setattr__(key, value)

    @property
    def patched_content(self):
        if self._old_content is None:
            return self.content
        return patch(self.delta, self._old_content)

    def save(self, *args, **kwargs):
        if self._old_content is not None:
            super().__setattr__('content', self.patched_content)
        super().save(*args, **kwargs)
        self._old_content = None

    @property
    def delta(self):
        if self._old_content is not None:
            return self._gen_delta(self._old_content, self.content)
        return list()

    def _gen_delta(self, original, modified):
        for action in diff(original, modified):
            if action[0] in self.actions:
                yield action

    def get_year_history(self):
        return self.get_history_period(years=1)

    def get_month_history(self):
        return self.get_history_period(months=1)

    def get_week_history(self):
        return self.get_history_period(weeks=1)

    def get_day_history(self):
        return self.get_history_period(days=1, zero_time=False)

    def get_history_period(self, zero_time=True, **kwargs):
        zero_time = {'hour': 0, 'minute': 0, 'second': 0, 'microsecond': 0} if zero_time else {}
        today = timezone.now()
        delta = relativedelta(**zero_time, **kwargs)
        last_date = today - delta
        print(last_date)
        return self.get_history(created__gte=last_date)

    def get_history(self, n=-1, **kwargs):
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise TypeError('Amount of history items must be integer representable: "%s" isn\'t' % (n,))
        current_version = self.content
        history = [current_version]
        doc_delta_set = DocDelta.objects.all().filter(**kwargs).order_by('-created')
        for doc_delta in doc_delta_set:
            previous_version = revert(doc_delta.delta, current_version)
            history.append(previous_version)
            current_version = previous_version
        if n >= 0:
            return history[:n]
        return history

    def __str__(self):
        return '%s%s' % (json.dumps(self.content)[:100], '...')

    def __repr__(self):
        return '<%s: id="%s" uid="%s">' % (self.__class__.__name__, self.id, self.uid,)


class TrackChangeMixin:
    _track_change = True


class TrackAddMixin:
    _track_add = True


class TrackRemoveMixin:
    _track_remove = True


class PersistentHistoryDocument(TrackChangeMixin, TrackAddMixin, GenericDocument):
    """
    Only updates and additional information are tracked
    All deletions will be ignored
    """

    class Meta:
        proxy = True


class UnsteadyHistoryDocument(TrackChangeMixin, TrackAddMixin, TrackRemoveMixin, GenericDocument):
    """
    All changes (add, updates and deletion) will be counted
    """

    class Meta:
        proxy = True


class Document(PersistentHistoryDocument):
    pass


class RemovableHistoryDocument(UnsteadyHistoryDocument):
    pass