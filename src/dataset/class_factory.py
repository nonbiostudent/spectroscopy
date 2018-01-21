import collections
from copy import deepcopy, copy
import datetime
import hashlib
import inspect
from uuid import uuid4
import warnings
import weakref

import numpy as np
import tables

import dataset.util

class ResourceIdentifier(object):
    """
    Unique identifier of any resource so it can be referred to.

    All elements of a Dataset instance have a unique id that other elements
    use to refer to it. This is called a ResourceIdentifier.

    In this class it can be any hashable object, e.g. most immutable objects
    like numbers and strings.

    :type id: str, optional
    :param id: A unique identifier of the element it refers to. It is
        not verified, that it actually is unique. The user has to take care of
        that. If no resource_id is given, uuid.uuid4() will be used to
        create one which assures uniqueness within one Python run.
        If no fixed id is provided, the ID will be built from prefix
        and a random uuid hash. The random hash part can be regenerated by the
        referred object automatically if it gets changed.
    :type prefix: str, optional
    :param prefix: An optional identifier that will be put in front of any
        automatically created resource id. The prefix will only have an effect
        if `id` is not specified (for a fixed ID string). Makes automatically
        generated resource ids more reasonable.
    :type referred_object: Python object, optional
    :param referred_object: The object this instance refers to. All instances
        created with the same resource_id will be able to access the object as
        long as at least one instance actual has a reference to it.

    .. rubric:: General Usage

    >>> ResourceIdentifier('2012-04-11--385392')
    ResourceIdentifier(id="2012-04-11--385392")
    >>> # If 'id' is not specified it will be generated automatically.
    >>> ResourceIdentifier()  # doctest: +ELLIPSIS
    ResourceIdentifier(id="...")
    >>> # Supplying a prefix will simply prefix the automatically generated ID
    >>> ResourceIdentifier(prefix='peru09')  # doctest: +ELLIPSIS
    ResourceIdentifier(id="peru09_...")

    ResourceIdentifiers can, and oftentimes should, carry a reference to the
    object they refer to. This is a weak reference which means that if the
    object gets deleted or runs out of scope, e.g. gets garbage collected, the
    reference will cease to exist.

    >>> class A(object): pass
    >>> a = A()
    >>> import sys
    >>> ref_count = sys.getrefcount(a)
    >>> res_id = ResourceIdentifier(referred_object=a)
    >>> # The reference does not change the reference count of the object.
    >>> print(ref_count == sys.getrefcount(a))
    True
    >>> # It actually is the same object.
    >>> print(a is res_id.get_referred_object())
    True
    >>> # Deleting it, or letting the garbage collector handle the object will
    >>> # invalidate the reference.
    >>> del a
    >>> print(res_id.get_referred_object())
    None

    The most powerful ability (and reason why one would want to use a resource
    identifier class in the first place) is that once a ResourceIdentifier with
    an attached referred object has been created, any other ResourceIdentifier
    instances with the same ID can retrieve that object. This works
    across all ResourceIdentifiers that have been instantiated within one
    Python run.
    This enables, e.g. the resource references between the different elements
    to work in a rather natural way.

    >>> a = A()
    >>> obj_id = id(a)
    >>> res_id = "someid"
    >>> ref_a = ResourceIdentifier(res_id)
    >>> # The object is refers to cannot be found yet. Because no instance that
    >>> # an attached object has been created so far.
    >>> print(ref_a.get_referred_object())
    None
    >>> # This instance has an attached object.
    >>> ref_b = ResourceIdentifier(res_id, referred_object=a)
    >>> ref_c = ResourceIdentifier(res_id)
    >>> # All ResourceIdentifiers will refer to the same object.
    >>> assert(id(ref_a.get_referred_object()) == obj_id)
    >>> assert(id(ref_b.get_referred_object()) == obj_id)
    >>> assert(id(ref_c.get_referred_object()) == obj_id)


    ResourceIdentifiers are considered identical if the IDs are
    the same.

    >>> # Create two different resource identifiers.
    >>> res_id_1 = ResourceIdentifier()
    >>> res_id_2 = ResourceIdentifier()
    >>> assert(res_id_1 != res_id_2)
    >>> # Equalize the IDs. NEVER do this. This is just an example.
    >>> res_id_2.id = res_id_1.id = "smi:local/abcde"
    >>> assert(res_id_1 == res_id_2)

    ResourceIdentifier instances can be used as dictionary keys.

    >>> dictionary = {}
    >>> res_id = ResourceIdentifier(id="foo")
    >>> dictionary[res_id] = "bar1"
    >>> # The same ID can still be used as a key.
    >>> dictionary["foo"] = "bar2"
    >>> items = sorted(dictionary.items(), key=lambda kv: kv[1])
    >>> for k, v in items:  # doctest: +ELLIPSIS
    ...     print repr(k), v
    ResourceIdentifier(id="foo") bar1
    ...'foo' bar2
    """
    # Class (not instance) attribute that keeps track of all resource
    # identifier throughout one Python run. Will only store weak references and
    # therefore does not interfere with the garbage collection.
    # DO NOT CHANGE THIS FROM OUTSIDE THE CLASS.
    __resource_id_weak_dict = weakref.WeakValueDictionary()
    # Use an additional dictionary to track all resource ids.
    __resource_id_tracker = collections.defaultdict(int)

    def __init__(self, oid=None, prefix=None,
                 referred_object=None):
        # Create a resource id if None is given and possibly use a prefix.
        if oid is None:
            self.fixed = False
            self._prefix = prefix
            self._uuid = str(uuid4())
        else:
            self.fixed = True
            self.id = oid
        # Append the referred object in case one is given to the class level
        # reference dictionary.
        if referred_object is not None:
            self.set_referred_object(referred_object)

        # Increment the counter for the current resource id.
        ResourceIdentifier.__resource_id_tracker[self.id] += 1

    def __del__(self):
        if self.id not in ResourceIdentifier.__resource_id_tracker:
            return
        # Decrement the resource id counter.
        ResourceIdentifier.__resource_id_tracker[self.id] -= 1
        # If below or equal to zero, delete it and also delete it from the weak
        # value dictionary.
        if ResourceIdentifier.__resource_id_tracker[self.id] <= 0:
            del ResourceIdentifier.__resource_id_tracker[self.id]
            try:
                del ResourceIdentifier.__resource_id_weak_dict[self.id]
            except KeyError:
                pass

    def get_referred_object(self):
        """
        Returns the object associated with the resource identifier.

        This works as long as at least one ResourceIdentifier with the same
        ID as this instance has an associate object.

        Will return None if no object could be found.
        """
        try:
            return ResourceIdentifier.__resource_id_weak_dict[self.id]
        except KeyError:
            return None

    def set_referred_object(self, referred_object):
        """
        Sets the object the ResourceIdentifier refers to.

        If it already a weak reference it will be used, otherwise one will be
        created. If the object is None, None will be set.

        Will also append self again to the global class level reference list so
        everything stays consistent.
        """
        # If it does not yet exists simply set it.
        if self.id not in ResourceIdentifier.__resource_id_weak_dict:
            ResourceIdentifier.__resource_id_weak_dict[self.id] = \
                referred_object
            return
        # Otherwise check if the existing element the same as the new one. If
        # it is do nothing, otherwise raise a warning and set the new object as
        # the referred object.
        if ResourceIdentifier.__resource_id_weak_dict[self.id] == \
                referred_object:
            return
        msg = "The resource identifier '%s' already exists and points to " + \
              "another object: '%s'." + \
              "It will now point to the object referred to by the new " + \
              "resource identifier."
        msg = msg % (
            self.id,
            repr(ResourceIdentifier.__resource_id_weak_dict[self.id]))
        # Always raise the warning!
        warnings.warn_explicit(msg, UserWarning, __file__,
                               inspect.currentframe().f_back.f_lineno)
        ResourceIdentifier.__resource_id_weak_dict[self.id] = \
            referred_object

    def copy(self):
        """
        Returns a copy of the ResourceIdentifier.

        >>> res_id = ResourceIdentifier()
        >>> res_id_2 = res_id.copy()
        >>> print(res_id is res_id_2)
        False
        >>> print(res_id == res_id_2)
        True
        """
        return deepcopy(self)

    @property
    def id(self):
        """
        Unique identifier of the current instance.
        """
        if self.fixed:
            return self.__dict__.get("id")
        else:
            oid = self.prefix
            if oid is not None and not oid.endswith("_"):
                oid += "_"
                oid += self.uuid
                return oid
            return self.uuid

    @id.deleter
    def id(self):
        msg = "The resource id cannot be deleted."
        raise Exception(msg)

    @id.setter
    def id(self, value):
        self.fixed = True
        # XXX: no idea why I had to add bytes for PY2 here
        if not isinstance(value, (str, bytes)):
            msg = "attribute id needs to be a string."
            raise TypeError(msg)
        self.__dict__["id"] = value

    @property
    def prefix(self):
        return self._prefix

    @prefix.deleter
    def prefix(self):
        self._prefix = ""

    @prefix.setter
    def prefix(self, value):
        if not isinstance(value, str):
            msg = "prefix id needs to be a string."
            raise TypeError(msg)
        self._prefix = value

    @property
    def uuid(self):
        return self._uuid

    @uuid.deleter
    def uuid(self):
        """
        Deleting is uuid hash is forbidden and will not work.
        """
        msg = "The uuid cannot be deleted."
        raise Exception(msg)

    @uuid.setter
    def uuid(self, value):  # @UnusedVariable
        """
        Setting is uuid hash is forbidden and will not work.
        """
        msg = "The uuid cannot be set manually."
        raise Exception(msg)

    @property
    def resource_id(self):
        return self.id

    @resource_id.deleter
    def resource_id(self):
        del self.id

    @resource_id.setter
    def resource_id(self, value):
        self.id = value

    def __str__(self):
        return self.id

    def _repr_pretty_(self, p, cycle):
        p.text(str(self))

    def __repr__(self):
        return 'ResourceIdentifier(id="%s")' % self.id

    def __eq__(self, other):
        if self.id == other:
            return True
        if not isinstance(other, ResourceIdentifier):
            return False
        if self.id == other.id:
            return True
        return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        """
        Uses the same hash as the resource id. This means that class instances
        can be used in dictionaries and other hashed types.

        Both the object and it's id can still be independently used as
        dictionary keys.
        """
        # "Salt" the hash with a string so the hash of the object and a
        # string identical to the id can both be used as individual
        # dictionary keys.
        return hash("RESOURCE_ID") + self.id.__hash__()

    def regenerate_uuid(self):
        """
        Regenerates the uuid part of the ID. Does nothing for resource
        identifiers with a user-set, fixed id.
        """
        self._uuid = str(uuid4())


class RetVal(object):
    """
    Wrapper to make tables.array.Array read only.
    """

    def __init__(self, wrapped_object):
        self.__dict__['_wrapped_object'] = wrapped_object
        attributes = dir(wrapped_object)
        for attr in attributes:
            if hasattr(self, attr):
                continue
            self.__dict__[attr] = attr

    def __setitem__(self, key, value):
        raise AttributeError('Data type is read only.')

    def __setslice__(self, i, j, value):
        raise AttributeError('Data type is read only.')

    def __setattr__(self, key, value):
        raise AttributeError('Data type is read only.')

    def __getattribute__(self, key):
        if key in ['_wrapped_object', '__dict__', '__class__']:
            return object.__getattribute__(self, key)
        return getattr(self._wrapped_object, key)

    def __getitem__(self,key):
        return self._wrapped_object.__getitem__(key)

    def __str__(self):
        return self._wrapped_object.__str__()


class H5Set(set):
    """
    An hdf5 set class for tags.
    """

    def __init__(self, h5node):
        self.h5node = h5node
        # check for already existing tags e.g. when 
        # reading in a file
        f = self.h5node._v_file
        try:
            for _t in f.root.tags._v_children:
                ea = f.root.tags._v_children[_t]
                entries = ea[np.where(ea[:] == self.h5node._v_name.encode())]
                if len(entries) > 0:
                    super(H5Set,self).add(_t)
        except (KeyError, tables.NoSuchNodeError):
            pass

    def add(self, val):
        f = self.h5node._v_file
        if val in self:
            return
        try:
            super(H5Set,self).add(val)
        except Exception,e:
            print val
            raise e
        try:
            ea = f.root.tags._v_children[val]
        except (KeyError,tables.NoSuchNodeError):
            msg = "Tag {:s} has not been registered yet. "
            msg += "Use the 'Dataset.register_tags' function first."
            raise ValueError(msg.format(val))
        found = False
        for i in range(ea.nrows):
            if ea[i] == '':
                ea[i] = np.array(
                    self.h5node._v_name, dtype='S60')
                found = True
                break
        if not found:
            ea.append(
                np.array([self.h5node._v_name], dtype='S60'))

    def append(self, val):
        """
        Append a tag to the list of existing tags.
        """
        self.add(val)

    def remove(self, val):
        f = self.h5node._v_file
        super(H5Set,self).remove(val)
        ea = f.root.tags._v_children[val]
        ea[np.where(ea[:] == self.h5node._v_name.encode())] = np.array(
            [''], dtype='S60')
        if np.all(ea[:] == np.array('', dtype='S60')):
            f.remove_node('/tags/' + val)

    def pop(self):
        val = set.pop(self)
        self.remove(val)
        return val

    def discard(self, val):
        try:
            self.remove(val)
        except KeyError:
            pass

    def clear(self):
        while True:
            try:
                self.pop()
            except:
                break

    def update(self, vals):
        for v in vals:
            self.add(v)

    def difference_update(self, vals):
        for v in vals:
            self.discard(v)


def _class_factory(class_name, class_type='base', class_attributes=[], class_references=[]):
    """
    Class factory to unify the creation of all the types in the datamodel.
    """

    class DataElementBase(object):
        """
        A base class with type checking for non-extendable elements in the datamodel.
        """
        # Assign properties of the data element including the expected data types
        _properties = []
        for item in class_attributes:
            _properties.append((item[0], item[1]))
        _property_keys = [_i[0] for _i in _properties]
        _property_dict = {}
        for key, val in _properties:
            _property_dict[key] = val

        # Assign references to other elements in the datamodel
        _references = []
        for item in class_references:
            _references.append((item[0],item[1]))
        _reference_keys = [_i[0] for _i in _references] 
        _reference_dict = {}
        for key, val in _references:
            _reference_dict[key] = val 

        # Map numpy types to pytables types
        dtmap = {np.float64: tables.Float64Atom(),
                 np.int64: tables.IntAtom(),
                 np.string_: tables.StringAtom(itemsize=128)}

        def __init__(self, h5node, data_buffer=None, pedantic=True):
            # Set the parent HDF5 group after type checking
            if (type(h5node) is not tables.group.Group):
                raise Exception("%s and %s are incompatible types." %
                                (type(h5node), tables.group.Group))
            self.__dict__['_root'] = h5node
            self.__dict__['_tags'] = H5Set(h5node)
            # Every time a new object is created it gets a new resource ID
            self.__dict__['_resource_id'] = ResourceIdentifier(oid=h5node._v_name,
                                                              referred_object=self)
            if not hasattr(h5node._v_attrs, 'creation_time'):
                self.__dict__['creation_time'] = datetime.datetime.utcnow().isoformat()
                h5node._v_attrs.creation_time = self.creation_time
            else:
                self.__dict__['creation_time'] = h5node._v_attrs.creation_time

            #add all the _properties as properties of the class, so that they 
            #can be seen by tab completion (otherwise the user cannot see what 
            #properties the class has without accessing the _properites dict)
            for att_name, junk in self._properties:
                
                #use of kwarg in lambda function required to prevent 'name' being
                #overriden by subsequent iterations of this for loop.
                p = property(lambda self, name=att_name: self.__getattr__(name))
                setattr(self.__class__, att_name, p)
            
            
            if data_buffer is not None:
                dtp = []
                vals = {}
                avals = {}
                for key, prop_type in self._property_dict.iteritems():
                    val = getattr(data_buffer,key,None)
                    if val is None:
                        continue
                    if key == 'tags':
                        for _v in val:
                            self._tags.add(_v)
                        continue
                    if prop_type[0] == datetime.datetime:
                        vals[key] = val 
                        dtp.append((key,np.dtype('S'+str(len(val))),()))
                    elif prop_type[0] == np.ndarray:
                        avals[key] = val
                    else: 
                        vals[key] = val
                        dtp.append((key,val.dtype,val.shape))

                for key, prop_type in self._reference_dict.iteritems():
                    val = getattr(data_buffer,key,None)
                    if val is None:
                        continue
                    vals[key] = val
                    # References are either strings or arrays of strings
                    if prop_type[0] == np.ndarray:
                        dtp.append((key,val.dtype,val.shape))
                    else:
                        dtp.append((key,np.dtype('S'+str(len(val))),()))            
                # Add a hash column to avoid adding the same entries more than once
                dtp.append(('hash','S28',()))
                # Allow to create empty elements for testing
                if len(dtp) > 0:
                    s = hashlib.sha224()
                    f = h5node._v_file
                    table = f.create_table(h5node,'data', np.dtype(dtp))
                    entry = table.row
                    for key,val in vals.iteritems():
                        entry[key]  = val
                        s.update('{}'.format(val))
                    for key, val in avals.iteritems():
                        try:
                            shape = list(val.shape)
                            shape[0] = 0
                            vl = f.create_earray(h5node, key, atom=self.dtmap[val.dtype.type],
                                                shape=tuple(shape))
                        except Exception, e:
                            print val.dtype.type
                            raise e
                        vl.append(val)
                        s.update('{}'.format(val))
                    h = s.digest()
                    entry['hash'] = h
                    f = self._root._v_file
                    ea = f.root.hash
                    if pedantic and h in ea:
                        msg = "You can't add the same dataset "
                        msg += "more than once if 'pedantic=True'."
                        raise ValueError(msg)
                    ea.append(np.array([h],dtype='S28'))
                    entry.append()
                    table.flush() 

        @property
        def tags(self):
            return self._tags

        def __str__(self):
            return class_name.strip('_')

        def __setattr__(self, name, value):
            # Raise an exception if not a property or attribute
            raise AttributeError(
                '{} attributes are read only. Use append method instead.'.format(type(self).__name__))

        def __getattr__(self, name):
            print "__getattr__(%s)"%name
            table = getattr(self._root,'data')
            if name in self._property_keys:
                if self._property_dict[name][0] == np.ndarray:
                    return RetVal(getattr(self._root,name))
                return RetVal(getattr(table.cols,name))[0]
            elif name in self._reference_keys:
                if self._reference_dict[name][0] == np.ndarray:
                    _t = []
                    for val in table[0][name]:
                        _t.append(ResourceIdentifier(val).get_referred_object())
                    return _t
                else:
                    return ResourceIdentifier(table[0][name]).get_referred_object()
            else:
                msg = "{0:s} is not a property or reference of class {1:s}"
                raise AttributeError(msg.format(name, type(self).__name__))

        def __repr__(self):
            msg = ''
            msg += "ID: {:s}\n".format(self._root._v_name)
            for n1 in self._root._v_children.iterkeys():
                if n1 == 'data':
                    continue
                msg += "{0:s}: {1:}\n".format(n1, getattr(self._root, n1).shape)
            
            table = self._root.data
            for n2 in table.cols._v_colnames:
                if n2 == 'hash':
                    continue
                msg += "{0:s}: {1:}\n".format(n2, table[0][n2])
            msg += "Created at: {:s}\n".format(self._root._v_attrs.creation_time)
            return msg

        def append(self,databuffer):
            """
            A base element can't be extended.
            """
            raise AttributeError("A base element can't be extended")
                

    class ExpandableDataElement(DataElementBase):
        """
        A base class with type checking for extendable elements in the datamodel.
        """
        def __init__(self, h5node, data_buffer=None, pedantic=True):
            super(ExpandableDataElement,self).__init__(h5node,data_buffer,pedantic)
            self.__dict__['modification_time'] = self.creation_time 
            h5node._v_attrs.modification_time = self.modification_time

        def __repr__(self):
            msg = ''
            msg += "ID: {:s}\n".format(self._root._v_name)
            for n1 in self._root._v_children.iterkeys():
                if n1 == 'data':
                    continue
                msg += "{0:s}: {1:}\n".format(n1, getattr(self._root, n1).shape)

            table = self._root.data
            for n2 in table.cols._v_colnames:
                if n2 == 'hash':
                    continue
                msg += "{0:s}: {1:}\n".format(n2, table[0][n2])
            msg += "Created at: {:s}\n".format(self._root._v_attrs.creation_time)
            msg += "Last modified at: {:s}\n".format(self._root._v_attrs.creation_time)
            return msg

        def append(self, databuffer, pedantic=True):
            table = getattr(self._root, 'data')
            s = hashlib.sha1()
            entry = table.row
            
            for prop_name in self._properties.keys():
                try:
                    key, val = databuffer.__dict__[prop_name]
                except KeyError:
                    if pedantic:
                        msg = ("{} is missing a value for the {} field. Cannot "
                               "append incomplete buffers when 'pedantic=True'."
                               " ".format(str(databuffer), prop_name))
                        raise ValueError(msg)
                    else:
                        continue
            
            for key,val in databuffer.__dict__.iteritems():
                if val is not None:
                    try:
                        prop_type = self._property_dict[key]
                    except KeyError:
                        prop_type = self._reference_dict[key]
                        
                    if prop_type[0] == np.ndarray:
                        vl = getattr(self._root,key)
                        vl.append(val)
                    else:
                        entry[key] = val
                    s.update('{}'.format(val))
                
            h = s.digest()
            if h in table[:]['hash']:
                raise ValueError('Entry already exists.')
            entry['hash'] = h
            entry.append()
            table.flush()
            self.__dict__['modification_time'] = datetime.datetime.utcnow().isoformat()
            self._root._v_attrs.modification_time = self.modification_time


    class DataElementBuffer(object):
        # Every element has to have an ID and a reference to the plugin
        # root node
        _properties = []
        for item in class_attributes:
            _properties.append((item[0], item[1]))
        _property_keys = [_i[0] for _i in _properties]
        _property_dict = {}
        for key, value in _properties:
            _property_dict[key] = value

        # Assign references to other elements in the datamodel
        _references = []
        for item in class_references:
            _references.append((item[0],item[1]))
        _reference_keys = [_i[0] for _i in _references] 
        _reference_dict = {}
        for key, value in _references:
            _reference_dict[key] = value 

        def __init__(self, **kwargs):
            # Set all property values to None or the kwarg value.
            for key, _ in self._properties:
                value = kwargs.pop(key, None)
                setattr(self, key, value)
            for key in self._reference_keys:
                value = kwargs.pop(key,None)
                setattr(self, key, value)
            
            if len(kwargs.keys()) > 0:
                msg = "The following names are not a "
                msg += "property or reference of class {:s}: "
                msg += ",".join(kwargs.keys())
                raise AttributeError(msg.format(type(self).__name__))

        def __str__(self):
            return class_name.strip('_')

        def __setattr__(self, name, value):
            try:
                attrib_type = self._property_dict[name]
            except KeyError:
                try:
                    attrib_type = self._reference_dict[name]
                except KeyError:
                    raise AttributeError(
                        "%s is not a property or reference of class %s" %
                        (name, type(self).__name__))
            
            # Try to convert values into the specified datatypes 
            if value is not None:
                try:
                    if name in self._reference_keys:
                        # check type for references
                        if attrib_type[0] == np.ndarray:
                            _t = []
                            for n in value:
                                if type(n) is not attrib_type[1]:
                                    msg = "{:s} has to be of type: {}"
                                    raise ValueError(msg.format(name, attrib_type[0]))
                                _t.append(str(getattr(n,'_resource_id')))
                            value = np.array(_t)
                        else:
                            if type(value) is not attrib_type[0]:
                                msg = "{:s} has to be of type: {}"
                                raise ValueError(msg.format(name, attrib_type[0]))
                            value = str(getattr(value,'_resource_id'))
                    else:
                        if attrib_type[0] == np.ndarray:
                            # if the array contains datetime we need to convert
                            # it into strings as pytables can't handle datetime
                            # objects
                            if attrib_type[1] == datetime.datetime:
                                _vals = []
                                for v in value:
                                    _vals.append(dataset.util.parse_iso_8601(v).isoformat())
                                value = np.array(_vals)
                            else:
                                value = np.array(value).astype(attrib_type[1])
                        elif self._property_dict[name][0] == datetime.datetime:
                            value = dataset.util.parse_iso_8601(value).isoformat()
                        else:
                            value = attrib_type[0](value)
                except ValueError:
                    msg = "'{:s}' can't be converted to: {}"
                    raise ValueError(msg.format(name, attrib_type))
 
            self.__dict__[name] = value

    if class_type == 'base':
        base_class = DataElementBase
    elif class_type == 'extendable':
        base_class = ExpandableDataElement
    elif class_type == 'buffer':
        base_class = DataElementBuffer
    # Set the class type name.
    setattr(base_class, "__name__", class_name)
    return base_class


class _DataElementWriter(object):
    
    def __init__(self):
        pass
 
