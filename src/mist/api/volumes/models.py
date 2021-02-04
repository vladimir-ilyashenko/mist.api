import uuid
import logging
import mongoengine as me

from mist.api.tag.models import Tag

from mist.api.ownership.mixins import OwnershipMixin
from mist.api.mongoengine_extras import MistDictField

from mist.api.volumes.controllers import StorageController

log = logging.getLogger(__name__)


class VolumeActions(me.EmbeddedDocument):
    attach = me.BooleanField(default=False)
    detach = me.BooleanField(default=False)
    delete = me.BooleanField(default=False)
    tag = me.BooleanField(default=False)


class Volume(OwnershipMixin, me.Document):
    """The basic block storage (volume) model"""

    id = me.StringField(primary_key=True, default=lambda: uuid.uuid4().hex)

    cloud = me.ReferenceField('Cloud', required=True,
                              reverse_delete_rule=me.CASCADE)
    owner = me.ReferenceField('Organization', required=True,
                              reverse_delete_rule=me.CASCADE)
    location = me.ReferenceField('CloudLocation',
                                 reverse_delete_rule=me.DENY)
    attached_to = me.ListField(me.ReferenceField('Machine',
                                                 reverse_delete_rule=me.PULL))

    size = me.IntField()
    name = me.StringField()
    external_id = me.StringField(required=True)
    actions = me.EmbeddedDocumentField(VolumeActions,
                                       default=lambda: VolumeActions())
    extra = MistDictField()

    missing_since = me.DateTimeField()

    meta = {
        'allow_inheritance': True,
        'collection': 'volumes',
        'indexes': [
            {
                'fields': ['cloud', 'external_id'],
                'sparse': False,
                'unique': True,
                'cls': False,
            },
        ],
    }

    def __init__(self, *args, **kwargs):
        super(Volume, self).__init__(*args, **kwargs)
        # Set `ctl` attribute.
        self.ctl = StorageController(self)

    @property
    def tags(self):
        """Return the tags of this volume."""
        return {tag.key: tag.value
                for tag in Tag.objects(resource_id=self.id,
                                       resource_type='volume')}

    def clean(self):
        self.owner = self.owner or self.cloud.owner

    def delete(self):
        super(Volume, self).delete()
        self.owner.mapper.remove(self)
        Tag.objects(resource_id=self.id, resource_type='volume').delete()
        try:
            if self.owned_by:
                self.owned_by.get_ownership_mapper(self.owner).remove(self)
        except Exception as exc:
            log.error("Got error %r while removing volume %s", exc, self.id)

    def as_dict(self):
        """Returns the API representation of the `Volume` object."""
        volume_dict = {
            'id': self.id,
            'cloud': self.cloud.id,
            'external_id': self.external_id,
            'name': self.name,
            'extra': self.extra,
            'tags': self.tags,
            'size': self.size,
            'location': self.location.id if self.location else None,
            'attached_to': [m.id for m in self.attached_to],
            'actions': {action: self.actions[action]
                        for action in self.actions},
            'owned_by': self.owned_by.id if self.owned_by else '',
            'created_by': self.created_by.id if self.created_by else '',
        }

        return volume_dict

    def as_dict_v2(self, deref='auto', only=''):
        """Returns the API representation of the `Volume` object."""
        from mist.api.helpers import prepare_dereferenced_dict
        standard_fields = ['id', 'name', 'external_id', 'extra']
        deref_map = {
            'cloud': 'title',
            'location': 'name',
            'owned_by': 'email',
            'created_by': 'email',
            'attached_to': 'name',
        }
        ret = prepare_dereferenced_dict(standard_fields, deref_map, self,
                                        deref, only)

        if 'tags' in only or not only:
            ret['tags'] = {
                tag.key: tag.value
                for tag in Tag.objects(
                    owner=self.owner,
                    resource_id=self.id,
                    resource_type='volume').only('key', 'value')
            }

        if 'actions' in only or not only:
            ret['actions'] = {
                action: self.actions[action] for action in self.actions
            }

        ret['size'] = "%dGB" % self.size if self.size else self.size

        return ret

    def __str__(self):
        return '%s "%s" (%s)' % (self.__class__.__name__, self.name, self.id)
