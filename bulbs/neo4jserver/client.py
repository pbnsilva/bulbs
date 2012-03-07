# -*- coding: utf-8 -*-
#
# Copyright 2012 James Thornton (http://jamesthornton.com)
# BSD License (see LICENSE for details)
#
"""

Bulbs supports pluggable backends. This is the Neo4j Server client.

"""
import os
import re
import urllib

from bulbs.config import Config, DEBUG
from bulbs.rest import GET, PUT, POST, DELETE
from bulbs.utils import json, urlsplit, quote_plus
from bulbs.utils import get_file_path, get_logger, build_path
from bulbs.registry import Registry

# specific to this client
from bulbs.client import Client, Response, Result
from bulbs.rest import Request, RESPONSE_HANDLERS
from bulbs.typesystem import JSONTypeSystem
from bulbs.groovy import GroovyScripts

# The default URI
NEO4J_URI = "http://localhost:7474/db/data/"

# The logger defined in Config
log = get_logger(__name__)

# Neo4j Server Resource Paths
vertex_path = "node"
edge_path = "relationship"
index_path = "index"
gremlin_path = "ext/GremlinPlugin/graphdb/execute_script"
cypher_path = "ext/CypherPlugin/graphdb/execute_query"


class Neo4jResult(Result):
    """
    Container class for a single result, not a list of results.

    :param result: The raw result.
    :type result: dict

    :param config: The graph Config object.
    :type config: Config 

    :ivar raw: The raw result.
    :ivar data: The data in the result.

    """

    def __init__(self,result, config):
        self.config = config

        # The raw result.
        self.raw = result

        # The data in the result.
        self.data = self._get_data(result)

        self.type_map = dict(node="vertex",relationship="edge")

    def get_id(self):
        """
        Returns the element ID.

        :rtype: int

        """
        uri = self.raw.get('self')
        return self._parse_id(uri)
       
    def get_type(self):
        """
        Returns the element's base type, either "vertex" or "edge".

        :rtype: str

        """
        uri = self.get_uri()
        neo4j_type = self._parse_type(uri)
        return self.type_map[neo4j_type]
        
    def get_map(self):
        """
        Returns the element's property map.

        :rtype: dict

        """
        return self.data

    def get_uri(self):
        """
        Returns the element URI.

        :rtype: str

        """
        return self.raw.get('self')
                 
    def get_outV(self):
        """
        Returns the ID of the edge's outgoing vertex (start node).

        :rtype: int

        """
        uri = self.raw.get('start')
        return self._parse_id(uri)
        
    def get_inV(self):
        """
        Returns the ID of the edge's incoming vertex (end node).

        :rtype: int

        """
        uri = self.raw.get('end')
        return self._parse_id(uri)

    def get_label(self):
        """
        Returns the edge label (relationship type).

        :rtype: str

        """
        return self.raw.get('type')

    def get_index_name(self):
        """
        Returns the index name.

        :rtype: str

        """
        return self.raw.get('name')
   
    def get_index_class(self):
        """
        Returns the index class, either "vertex" or "edge".

        :rtype: str

        """
        uri = self.raw.get('template') 
        neo4j_type = self._parse_index_type(uri)
        return self.type_map[neo4j_type]

    def get(self,attribute):
        """
        Returns the value of a client-specific attribute.
        
        :param attribute: Name of the attribute. 
        :type attribute: str

        :rtype: str

        """
        return self.raw[attribute]

    def _get_data(self,result):
        if type(result) is dict:
            return result.get('data') 

    def _parse_id(self,uri):
        """Parses the ID out of a URI."""
        if uri:
            _id = int(uri.rpartition('/')[-1])
            return _id

    def _parse_type(self,uri):
        """Parses the type ouf of a normal URI."""
        if uri:
            root_uri = uri.rpartition('/')[0]
            neo4j_type = root_uri.rpartition('/')[-1]
            return neo4j_type
    
    def _parse_index_type(self,uri):
        """Parses the type out of an index URI."""
        if uri:
            path = urlsplit(uri).path
            segments = path.split("/")
            neo4j_type = segments[-4]
            return neo4j_type


class Neo4jResponse(Response):
    """
    Container class for the server response.

    :param response: httplib2 response: (headers, content).
    :type response: tuple

    :param config: Config object.
    :type config: bulbs.config.Config

    :ivar config: Config object.
    :ivar headers: httplib2 response headers, see:
        http://httplib2.googlecode.com/hg/doc/html/libhttplib2.html
    :ivar content: A dict containing the HTTP response content.
    :ivar results: A generator of Neo4jResult objects, a single Neo4jResult object, 
        or None, depending on the number of results returned.
    :ivar total_size: The number of results returned.
    :ivar raw: Raw HTTP response. Only set when log_level is DEBUG.

    """
    result_class = Neo4jResult

    def __init__(self, response, config):

        self.config = config
        self.handle_response(response)
        self.headers = self.get_headers(response)
        self.content = self.get_content(response)
        self.results, self.total_size = self.get_results()
        self.raw = self._maybe_get_raw(response, config)

    def _maybe_get_raw(self,response, config):
        # don't store raw response in production else you'll bloat the obj
        if config.log_level == DEBUG:
            return response

    def handle_response(self,response):
        """
        Handle HTTP server response.
        
        :param response: httplib2 response: (headers, content).
        :type response: tuple

        :rtype: None

        """
        headers, content = response
        response_handler = RESPONSE_HANDLERS.get(headers.status)
        response_handler(response)

    def get_headers(self,response):
        """
        Return the headers from the HTTP response.

        :param response: httplib2 response: (headers, content).
        :type response: tuple
        
        :rtype: httplib2.Response

        """
        # response is a tuple containing (headers, content)
        # headers is an httplib2 Response object, content is a string
        # see http://httplib2.googlecode.com/hg/doc/html/libhttplib2.html
        headers, content = response
        return headers

    def get_content(self,response):
        """
        Return the content from the HTTP response.
        
        :param response: httplib2 response: (headers, content).
        :type response: tuple
        
        :rtype: dict or None

        """
        # content is a string
        headers, content = response

        # Neo4jServer returns empty content on update
        if content:
            content = json.loads(content.decode('utf-8'))
            return content

    def get_results(self):
        """
        Return results from response, formatted as Result objects, and its total size.

        :return:  A tuple containing: A generator of Neo4jResult objects, a single 
                  Neo4jResult object, or None, depending on the number of results 
                  returned; An int of the number results returned.
        :rtype: tuple

        """
        if type(self.content) == list:
            results = (self.result_class(result, self.config) for result in self.content)
            total_size = len(self.content)
        elif self.content and self.content != "null":
            # Checking for self.content.get('data') won't work b/c the data value
            # isn't returned for edges with no properties;
            # and self.content != "null": Yep, the null thing is sort of a hack. 
            # Neo4j returns "null" if Gremlin scripts don't return anything.
            results = self.result_class(self.content, self.config)
            total_size = 1
        else:
            results = None
            total_size = 0
        return results, total_size


    def _set_index_name(self,index_name):
        """Sets the index name to the raw result."""
        # this is pretty much a hack becuase the way neo4j does this is inconsistent
        self.results.raw['name'] = index_name
        

class Neo4jRequest(Request):
    """Makes HTTP requests to Neo4j Server and returns a Neo4jResponse.""" 
    
    response_class = Neo4jResponse



class Neo4jClient(Client):
    """
    Low-level client that sends a request to Neo4j Server and returns a response.

    :param config: Optional Config object. Defaults to default Config.
    :type config: Config

    :ivar config: Config object.
    :ivar scripts: GroovyScripts object.  
    :ivar type_system: JSONTypeSystem object.
    :ivar request: Neo4jRequest object.

    Example:

    >>> from bulbs.neo4jserver import Neo4jClient
    >>> client = Neo4jClient()
    >>> script = client.scripts.get("get_vertices")
    >>> response = client.gremlin(script, params=None)
    >>> result = response.results.next()

    """ 
    #: Default URI for the database.
    default_uri = NEO4J_URI
    request_class = Neo4jRequest

    def __init__(self, config=None):

        self.config = config or Config(self.default_uri)
        self.registry = Registry(self.config)

        # Neo4j supports Gremlin so include the Gremlin-Groovy script library
        self.scripts = GroovyScripts()
        
        # Also include the Neo4j Server-specific Gremlin-Groovy scripts
        scripts_file = get_file_path(__file__, "gremlin.groovy")
        self.scripts.update(scripts_file)

        # Add it to the registry. This allows you to have more than one scripts namespace.
        self.registry.add_scripts("gremlin", self.scripts)

        self.type_system = JSONTypeSystem()
        self.request = self.request_class(self.config, self.type_system.content_type)
        
    #: Gremlin

    def gremlin(self, script, params=None): 
        """
        Executes a Gremlin script and returns the Response.

        :param script: Gremlin script to execute.
        :type script: str

        :param params: Param bindings for the script.
        :type params: dict

        :rtype: Neo4jResponse

        """
        path = gremlin_path
        params = dict(script=script, params=params)
        return self.request.post(path, params)

    # Cypher

    def cypher(self, query, params=None):
        """
        Executes a Cypher query and returns the Response.

        :param query: Cypher query to execute.
        :type query: str

        :param params: Param bindings for the query.
        :type params: dict

        :rtype: Neo4jResponse

        """
        path = cypher_path
        params = dict(query=query,params=params)
        resp = self.request.post(path, params)

        # Cypher data hack
        resp.results = (self.result_class(result[0], self.config) for result in resp.results.data)
        resp.total_size = len(resp.results.data)
        return resp

    #: Vertex Proxy

    def create_vertex(self, data):
        """
        Creates a vertex and returns the Response.

        :param data: Property data.
        :type data: dict

        :rtype: Neo4jResponse

        """
        if self.config.autoindex is True:
            index_name = self.config.vertex_index
            return self.create_indexed_vertex(data, index_name, keys=None)
        path = vertex_path
        params = self._remove_null_values(data)
        return self.request.post(path, params)

    def get_vertex(self, _id):
        """
        Gets the vertex with the _id and returns the Response.

        :param data: Vertex ID.
        :type data: int

        :rtype: Neo4jResponse

        """
        path = build_path(vertex_path, _id)
        params = None
        return self.request.get(path, params)
        
    def update_vertex(self, _id, data):
        """
        Updates the vertex with the _id and returns the Response.

        :param _id: Vertex ID.
        :type _id: dict

        :param data: Property data.
        :type data: dict

        :rtype: Neo4jResponse

        """
        if self.config.autoindex is True:
            index_name = self.config.vertex_index
            return self.update_indexed_vertex(_id,data,index_name,keys=None)
        path = self._build_vertex_path(_id,"properties")
        params = self._remove_null_values(data)
        return self.request.put(path, params)

    def delete_vertex(self, _id):
        """
        Deletes a vertex with the _id and returns the Response.

        :param _id: Vertex ID.
        :type _id: dict

        :rtype: Neo4jResponse

        """
        script = self.scripts.get("delete_vertex")
        params = dict(_id=_id)
        return self.gremlin(script,params)
        
    #: Edge Proxy

    def create_edge(self, outV, label, inV, data={}): 
        """
        Creates a edge and returns the Response.
        
        :param outV: Outgoing vertex ID.
        :type outV: int

        :param label: Edge label.
        :type label: str

        :param inV: Incoming vertex ID.
        :type inV: int

        :param data: Property data.
        :type data: dict

        :rtype: Neo4jResponse

        """
        if self.config.autoindex is True:
            index_name = self.config.edge_index
            return self.create_indexed_edge(outV,label,inV,data,index_name,keys=None)
        data = self._remove_null_values(data)
        inV_uri = self._build_vertex_uri(inV)
        path = build_path(vertex_path, outV, "relationships")
        params = {'to':inV_uri, 'type':label, 'data':data}
        return self.request.post(path, params)

    def get_edge(self, _id):
        """
        Gets the edge with the _id and returns the Response.

        :param data: Edge ID.
        :type data: int

        :rtype: Neo4jResponse

        """
        path = build_path(edge_path,_id)
        params = None
        return self.request.get(path, params)
        
    def update_edge(self, _id, data):
        """
        Updates the edge with the _id and returns the Response.

        :param _id: Edge ID.
        :type _id: dict

        :param data: Property data.
        :type data: dict

        :rtype: Neo4jResponse

        """
        if self.config.autoindex is True:
            index_name = self.config.edge_index
            return self.update_indexed_edge(_id,data,index_name,keys=None)
        path = build_path(edge_path,_id,"properties")
        params = self._remove_null_values(data)
        return self.request.put(path, params)

    def delete_edge(self, _id):
        """
        Deletes a edge with the _id and returns the Response.

        :param _id: Edge ID.
        :type _id: dict

        :rtype: Neo4jResponse

        """
        path = build_path(edge_path,_id)
        params = None
        return self.request.delete(path, params)

    # Vertex Container

    def outE(self, _id, label=None):
        """
        Returns the outgoing edges of the vertex.

        :param _id: Vertex ID.
        :type _id: dict

        :param label: Optional edge label. Defaults to None.
        :type label: str

        :rtype: Neo4jResponse
        
        """
        script = self.scripts.get('outE')
        params = dict(_id=_id,label=label)
        return self.gremlin(script,params)

    def inE(self, _id, label=None):
        """
        Returns the incoming edges of the vertex.

        :param _id: Vertex ID.
        :type _id: dict

        :param label: Optional edge label. Defaults to None.
        :type label: str

        :rtype: Neo4jResponse

        """
        script = self.scripts.get('inE')
        params = dict(_id=_id,label=label)
        return self.gremlin(script,params)

    def bothE(self, _id, label=None):
        """
        Returns the incoming and outgoing edges of the vertex.

        :param _id: Vertex ID.
        :type _id: dict

        :param label: Optional edge label. Defaults to None.
        :type label: str

        :rtype: Neo4jResponse
        
        """
        script = self.scripts.get('bothE')
        params = dict(_id=_id,label=label)
        return self.gremlin(script,params)

    def outV(self, _id, label=None):
        """
        Returns the out-adjacent vertices of the vertex.

        :param _id: Vertex ID.
        :type _id: dict

        :param label: Optional edge label. Defaults to None.
        :type label: str

        :rtype: Neo4jResponse

        """
        script = self.scripts.get('outV')
        params = dict(_id=_id,label=label)
        return self.gremlin(script,params)
        
    def inV(self, _id, label=None):
        """
        Returns the in-adjacent vertices of the vertex.

        :param _id: Vertex ID.
        :type _id: dict

        :param label: Optional edge label. Defaults to None.
        :type label: str

        :rtype: Neo4jResponse

        """
        script = self.scripts.get('inV')
        params = dict(_id=_id,label=label)
        return self.gremlin(script,params)
        
    def bothV(self, _id, label=None):
        """
        Returns the incoming- and outgoing-adjacent vertices of the vertex.

        :param _id: Vertex ID.
        :type _id: dict

        :param label: Optional edge label. Defaults to None.
        :type label: str

        :rtype: Neo4jResponse

        """
        script = self.scripts.get('bothV')
        params = dict(_id=_id,label=label)
        return self.gremlin(script,params)

    #: Index Proxy - Vertex

    def create_vertex_index(self, index_name, *args, **kwds):
        """
        Creates a vertex index with the specified params.

        :param index_name: Name of the index to create.
        :type index_name: str

        :rtype: Neo4jResponse

        """
        index_type = kwds.pop("index_type", "exact")
        provider = kwds.pop("provider", "lucene")
        #keys = kwds.pop("keys",None)
        #config = {'type':index_type,'provider':provider,'keys':str(keys)}
        config = {'type':index_type, 'provider':provider}
        path = build_path(index_path, "node")
        params = dict(name=index_name, config=config)
        resp = self.request.post(path, params)
        resp._set_index_name(index_name)        
        return resp

    def get_vertex_indices(self):
        """
        Returns a map of all the vertex indices.

        :rtype: Neo4jResponse

        """
        path = build_path(index_path,"node")
        params = None
        return self.request.get(path, params)

    def get_vertex_index(self, index_name):
        """
        Returns the vertex index with the index_name.

        :param index_name: Name of the index.
        :type index_name: str

        :rtype: Neo4jResponse

        """
        resp = self.get_vertex_indices()
        resp.results = self._get_index_results(index_name,resp)
        if resp.results:
            resp._set_index_name(index_name)
        return resp

    def delete_vertex_index(self, index_name): 
        """
        Deletes the vertex index with the index_name.

        :param index_name: Name of the index.
        :type index_name: str

        :rtype: Neo4jResponse

        """
        path = build_path(index_path, "node", index_name)
        params = None
        return self.request.delete(path, params)

    # Index Proxy - Edge

    def create_edge_index(self, index_name, *args, **kwds):
        """
        Creates a edge index with the specified params.

        :param index_name: Name of the index.
        :type index_name: str

        :rtype: Neo4jResponse

        """
        path = build_path(index_path, edge_path)
        params = dict(name=index_name)
        resp = self.request.post(path, params)
        resp._set_index_name(index_name)
        return resp

    def get_edge_indices(self):
        """
        Returns a map of all the vertex indices.

        :rtype: Neo4jResponse

        """
        path = build_path(index_path,edge_path)
        params = None
        return self.request.get(path, params)

    def get_edge_index(self, index_name):
        """
        Returns the edge index with the index_name.

        :param index_name: Name of the index.
        :type index_name: str

        :rtype: Neo4jResponse

        """
        resp = self.get_edge_indices()
        resp.results = self._get_index_results(index_name, resp)
        if resp.results:
            resp._set_index_name(index_name)
        return resp

    def delete_edge_index(self, index_name):
        """
        Deletes the edge index with the index_name.

        :param index_name: Name of the index.
        :type index_name: str

        :rtype: Neo4jResponse

        """
        path = build_path(index_path, edge_path, index_name)
        params = None
        return self.request.delete(path, params)

    # Index Container - Vertex

    def put_vertex(self, index_name, key, value, _id):
        """
        Adds a vertex to the index with the index_name.

        :param index_name: Name of the index.
        :type index_name: str

        :param key: Name of the key.
        :type key: str

        :param value: Value of the key.
        :type value: str

        :param _id: Vertex ID
        :type _id: int
        
        :rtype: Neo4jResponse

        """
        uri = "%s/%s/%d" % (self.config.root_uri, "node", _id)
        path = build_path(index_path, "node", index_name)
        params = dict(key=key, value=value, uri=uri)
        return self.request.post(path, params)

    def lookup_vertex(self, index_name, key, value):
        """
        Returns the vertices indexed with the key and value.

        :param index_name: Name of the index.
        :type index_name: str

        :param key: Name of the key.
        :type key: str

        :param value: Value of the key.
        :type value: str

        :rtype: Neo4jResponse

        """
        key, value = quote_plus(key), quote_plus(value)
        path = build_path(index_path, "node", index_name, key, value)
        params = None
        return self.request.get(path, params)

    def query_vertex(self, index_name, params):
        """
        Returns the vertices for the index query.

        :param index_name: Name of the index.
        :type index_name: str

        :param params: Query params.
        :type params: dict

        :rtype: Neo4jResponse

        """
        path = build_path(index_path,"node",name)
        params = params
        return self.request.get(path, params)

    def remove_vertex(self, index_name, _id, key=None, value=None):
        """
        Removes a vertex from the index and returns the Response.

        :param index_name: Name of the index.
        :type index_name: str

        :param key: Optional. Name of the key.
        :type key: str

        :param value: Optional. Value of the key.
        :type value: str        

        :rtype: Neo4jResponse

        """
        path = build_path("node",name,key,value,_id)
        params = None
        return self.request.delete(path, params)

    # Index Container - Edge

    def put_edge(self, index_name, key, value, _id):
        """
        Adds an edge to the index and returns the Response.

        :param index_name: Name of the index.
        :type index_name: str

        :param key: Name of the key.
        :type key: str

        :param value: Value of the key.
        :type value: str

        :param _id: Edge ID
        :type _id: int
        
        :rtype: Neo4jResponse

        """
        uri = "%s/%s/%d" % (self.config.root_uri,edge_path,_id)
        path = build_path(index_path,edge_path,name)
        params = dict(key=key,value=value,uri=uri)
        return self.request.post(path, params)

    def lookup_edge(self, index_name, key, value):
        """
        Looks up an edge in the index and returns the Response.

        :param index_name: Name of the index.
        :type index_name: str

        :param key: Name of the key.
        :type key: str

        :param value: Value of the key.
        :type value: str

        :rtype: Neo4jResponse

        """
        key, value = quote_plus(key), quote_plus(value)
        path = build_path(index_path,edge_path,name,key,value)
        params = None
        return self.request.get(path, params)

    def query_edge(self, index_name, params):
        """
        Queries for an edge in the index and returns the Response.

        :param index_name: Name of the index.
        :type index_name: str
        
        :param params: Query params.
        :type params: dict

        :rtype: Neo4jResponse

        """
        path = build_path(index_path,edge_path,name)
        params = params
        return self.request.get(path, params)

    def remove_edge(self, index_name, _id, key=None, value=None):
        """
        Removes an edge from the index and returns the Response.
        
        :param index_name: Name of the index.
        :type index_name: str

        :param _id: Edge ID
        :type _id: int

        :param key: Optional. Name of the key.
        :type key: str

        :param value: Optional. Value of the key.
        :type value: str        

        :rtype: Neo4jResponse

        """
        path = build_path(edge_path,name,key,value,_id)
        params = None
        return self.request.delete(path, params)

    # Model Proxy - Vertex

    def create_indexed_vertex(self, data, index_name, keys=None):
        """
        Creates a vertex, indexes it, and returns the Response.

        :param data: Property data.
        :type data: dict

        :param index_name: Name of the index.
        :type index_name: str

        :param keys: Property keys to index.
        :type keys: list

        :rtype: Neo4jResponse

        """
        data = self._remove_null_values(data)
        params = dict(data=data,index_name=index_name,keys=keys)
        script = self.scripts.get("create_indexed_vertex")
        return self.gremlin(script,params)
    
    def update_indexed_vertex(self, _id, data, index_name, keys=None):
        """
        Updates an indexed vertex and returns the Response.

        :param index_name: Name of the index.
        :type index_name: str

        :param data: Property data.
        :type data: dict

        :param index_name: Name of the index.
        :type index_name: str

        :param keys: Property keys to index.
        :type keys: list

        :rtype: Neo4jResponse

        """
        data = self._remove_null_values(data)
        params = dict(_id=_id,data=data,index_name=index_name,keys=keys)
        script = self.scripts.get("update_indexed_vertex")
        return self.gremlin(script,params)

    # Model Proxy - Edge

    def create_indexed_edge(self, outV, label, inV, data, index_name, keys=None):
        """
        Creates a edge, indexes it, and returns the Response.

        :param outV: Outgoing vertex ID.
        :type outV: int

        :param label: Edge label.
        :type label: str

        :param inV: Incoming vertex ID.
        :type inV: int

        :param data: Property data.
        :type data: dict

        :param index_name: Name of the index.
        :type index_name: str

        :param keys: Property keys to index. Defaults to None (indexes all properties).
        :type keys: list

        :rtype: Neo4jResponse

        """
        data = self._remove_null_values(data)
        edge_params = dict(outV=outV,label=label,inV=inV)
        params = dict(data=data,index_name=index_name,keys=keys)
        params.update(edge_params)
        script = self.scripts.get("create_indexed_edge")
        return self.gremlin(script,params)


    def update_indexed_edge(self, _id, data, index_name, keys=None):
        """
        Updates an indexed edge and returns the Response.

        :param _id: Edge ID.
        :type _id: int

        :param data: Property data.
        :type data: dict

        :param index_name: Name of the index.
        :type index_name: str

        :param keys: Property keys to index. Defaults to None (indexes all properties).
        :type keys: list

        :rtype: Neo4jResponse

        """
        data = self._remove_null_values(data)
        params = dict(_id=_id,data=data,index_name=index_name,keys=keys)
        script = self.scripts.get("update_indexed_edge")
        return self.gremlin(script,params)


    # Metadata

    def get_metadata(self, key, default_value=None):
        script = self.scripts.get("get_metadata")
        params = dict(key=key, default_value=default_value)
        return self.gremlin(script, params)

    def set_metadata(self, key, value):
        script = self.scripts.get("set_metadata")
        params = dict(key=key, value=value)
        return self.gremlin(script, params)

    def remove_metadata(self, key):
        script = self.scripts.get("remove_metadata")
        params = dict(key=key)
        return self.gremlin(script, params)


    # Private 

    def _remove_null_values(self,data):
        """Removes null property values because they aren't valid in Neo4j."""
        # Neo4j Server uses PUTs to overwrite all properties so no need
        # to worry about deleting props that are being set to null.
        clean_data = [(k, data[k]) for k in data if data[k] is not None] # Python 3
        return dict(clean_data)

    def _get_index_results(self, index_name, resp):
        """
        Returns the index from a map of indicies.

        """
        if resp.content and index_name in resp.content:
            result = resp.content[index_name]
            return Neo4jResult(result, self.config)


    # Batch related
    def _placeholder(self,_id):
        pattern = "^{.*}$"
        match = re.search(pattern,str(_id))
        if match:
            placeholder = match.group()
            return placeholder

    def _build_vertex_path(self,_id,*args):
        # if the _id is a placeholder, return the placeholder;
        # othewise, return a normal vertex path
        placeholder = self._placeholder(_id) 
        if placeholder:
            segments = [placeholder]
        else:
            segments = [vertex_path,_id]
        segments = segments + list(args)
        return build_path(*segments)
        
    def _build_vertex_uri(self,_id,*args):
        placeholder = self._placeholder(_id) 
        if placeholder:
            return placeholder
        root_uri = self.config.root_uri.rstrip("/")
        segments = [vertex_path, _id] + list(args)
        path = build_path(*segments)
        uri = "%s/%s" % (root_uri, path)
        return uri

    def _build_edge_path(self,_id):
        # if the _id is a placeholder, return the placeholder;
        # othewise, return a normal edge path
        return self._placeholder(_id) or build_path(edge_path,_id)

    def _build_edge_uri(self,_id):
        pass
