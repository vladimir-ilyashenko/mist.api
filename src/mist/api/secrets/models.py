import logging
import mongoengine as me
from uuid import uuid4

from mist.api.users.models import Owner
from mist.api.ownership.mixins import OwnershipMixin
from mist.api.tag.models import Tag
from mist.api.secrets import controllers

from mist.api import config


log = logging.getLogger(__name__)


class Secret(OwnershipMixin, me.Document):
    """ A Secret object """
    id = me.StringField(primary_key=True,
                        default=lambda: uuid4().hex)
    name = me.StringField(required=True)
    owner = me.ReferenceField(Owner, reverse_delete_rule=me.CASCADE)

    meta = {
        'strict': False,
        'allow_inheritance': True,
        'collection': 'secrets',
        'indexes': [
            'owner',
            {
                'fields': ['owner', 'name'],
                'sparse': False,
                'unique': True,
                'cls': False,
            },
        ],
    }

    _controller_cls = None

    def __init__(self, *args, **kwargs):
        super(Secret, self).__init__(*args, **kwargs)
        # Set attribute `ctl` to an instance of the appropriate controller.
        if self._controller_cls is None:
            raise NotImplementedError(
                "Can't initialize %s. Secret is an abstract base class and "
                "shouldn't be used to create cloud instances. All Secret "
                "subclasses should define a `_controller_cls` class attribute "
                "pointing to a `BaseSecretController` subclass." % self
            )
        elif not issubclass(self._controller_cls,
                            controllers.BaseSecretController):
            raise TypeError(
                "Can't initialize %s.  All Secret subclasses should define a"
                " `_controller_cls` class attribute pointing to a "
                "`BaseSecretController` subclass." % self
            )

        self.ctl = self._controller_cls(self)

        # Calculate and store key type specific fields.
        self._secret_specific_fields = [field for field in type(self)._fields
                                        if field not in Secret._fields]

    @property
    def data(self):
        raise NotImplementedError()

    def __str__(self):
        return '%s secret %s (%s) of %s' % (type(self), self.name,
                                            self.id, self.owner)

    @property
    def tags(self):
        """Return the tags of this secret."""
        return {tag.key: tag.value
                for tag in Tag.objects(resource_id=self.id,
                                       resource_type='secret')}

    def delete(self):
        super(Secret, self).delete()
        self.owner.mapper.remove(self)
        Tag.objects(resource_id=self.id, resource_type='secret').delete()

    def as_dict(self):
        s_dict = {
            'id': self.id,
            'name': self.name,
            'tags': self.tags,
            'owned_by': self.owned_by.id if self.owned_by else '',
            'created_by': self.created_by.id if self.created_by else '',
        }
        return s_dict


class VaultSecret(Secret):
    """ A Vault Secret object """
    _controller_cls = controllers.VaultSecretController

    def __init__(self, *args, **kwargs):
        if config.VAULT_KV_VERSION == 1:
            self._controller_cls = controllers.KV1VaultSecretController
        else:
            self._controller_cls = controllers.KV2VaultSecretController

        super(VaultSecret, self).__init__(*args, **kwargs)

    @property
    def data(self):
        return self.ctl.read_secret()


class SecretValue(me.EmbeddedDocument):
    """ Retrieve the value of a Secret object """
    secret = me.ReferenceField('Secret', required=False)
    key = me.StringField()

    def __init__(self, secret, key='', *args, **kwargs):
        super(SecretValue, self).__init__(*args, **kwargs)
        self.secret = secret
        if key:
            self.key = key

    @property
    def value(self):
        if self.key:
            return self.secret.data[self.key]
        else:
            return self.secret.data

    def __str__(self):
        return '%s secret value of %s' % (type(self),
                                          self.secret.name)
