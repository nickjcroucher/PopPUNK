import json
import h5py
import os 
import re
import sys
import numpy as np
import pandas as pd
import graph_tool.all as gt
import requests 

class ArgsStructure:
    def __init__(self, **entries):
        self.__dict__.update(entries)

def default_options(species_db):
    """Default options for WebAPI"""
    with open(os.path.join(species_db, "args.txt")) as a:
        args_json = a.read()
    args_dict = json.loads(args_json)
    args = ArgsStructure(**args_dict)
    args.ref_db = species_db
    args.previous_clustering = species_db
    args.model_dir = species_db
    args.distances = os.path.join(species_db, species_db + ".dists")
    args.q_files = os.path.join(args.output, "queries.txt")
    return (args)

def get_colours(query, clusters):
    """Colour array for Plotly.js"""
    colours = []
    query = '"' + str(query) + '"'
    for clus in clusters:
        if not clus == query:
            colours.append('"blue"')
        else:
            colours.append('"rgb(255,128,128)"')
    return colours

def sketch_to_hdf5(sketch, output):
    """Convert JSON sketch to query hdf5 database"""
    kmers = []
    dists = []

    sketch_dict = json.loads(sketch)
    qNames = ["query"]
    queryDB = h5py.File(os.path.join(output, output + '.h5'), 'w')
    sketches = queryDB.create_group("sketches")
    sketch_props = sketches.create_group(qNames[0])

    for key, value in sketch_dict.items():
        try:
            kmers.append(int(key))
            dists.append(np.array(value))
        except:
            if key == "bases":
                sketch_props.attrs['base_freq'] = value
            elif key == "bbits":
                sketch_props.attrs['bbits'] = value
            elif key == "length":
                sketch_props.attrs['length'] = value
            elif key == "missing_bases":
                sketch_props.attrs['missing_bases'] = value
            elif key == "sketchsize64":
                sketch_props.attrs['sketchsize64'] = value
            elif key == "version":
                sketches.attrs['sketch_version'] = value
            elif key == "codon_phased":
                sketches.attrs['codon_phased'] = value
            elif key == "densified":
                sketches.attrs['densified'] = value
            else:
                raise AttributeError(key + " Not recognised")
    
    sketch_props.attrs['kmers'] = kmers
    for k_index in range(len(kmers)):
        k_spec = sketch_props.create_dataset(str(kmers[k_index]), data=dists[k_index], dtype='uint64')
        k_spec.attrs['kmer-size'] = kmers[k_index]
    queryDB.close()
    return qNames

def ReformatNode(node_list):
    """Graphml node to JSON"""
    json_nodes = []
    for node in node_list:
        id = node.split('"')[1]
        label = re.search('<data key="key0">(.*)</data>', node).group(1)
        data = '{"data": {"id":"' + id + '", "label":"' + label + '"}}'
        json_nodes.append(data)
    return json_nodes

def ReformatEdge(edge_list):
    """Graphml edge to JSON"""
    json_edges = []
    for edge in edge_list:
        split = edge.split('"')
        id = split[1]
        source = split[3]
        target = split[5]
        try:
            weight = re.search('">(.*)</', edge.split("<data")[1]).group(1)
            data = '{"data": {"id":"' + id + '", "source":"' + source + '", "target":"' + target + '","weight":' + str(weight) + '}}'
        except:
            data = '{"data": {"id":"' + id + '", "source":"' + source + '", "target":"' + target + '}}'
            print("No weights specified")
        json_edges.append(data)
    return json_edges

def clean_network(json):
    """Graphml subgraph network to JSON """
    json = json.split('<node')
    nodes = json[1:]
    print(len(nodes))
    edges = json[-1].split('</edge>')[:-1]
    json_nodes = ReformatNode(nodes)
    try:
        edges[0] = "".join(edges[0].split("<edge")[1:])
        json_edges = ReformatEdge(edges)
        jsonNetwork = '{"elements":{"nodes":[' + ",".join(json_nodes) + '],"edges":[' + ",".join(json_edges) + ']}}'
    except:
        jsonNetwork = '{"elements":{"nodes":[{"data": {"id":"0", "label":"query"}}]}}'
        print("No edges present")
    return jsonNetwork

def graphml_to_json(query, output):
    """Converts full GraphML file to JSON subgraph"""
    query = int(query) - 1
    G = gt.load_graph(os.path.join(output, output + ".graphml"))
    components = gt.label_components(G)[0].a
    subgraph = gt.GraphView(G, vfilt=(components == int(query)))
    subgraph = gt.Graph(subgraph, prune=True)
    subgraph.save(os.path.join(output,"subgraph.graphml")) 
    
    with open(os.path.join(output,"subgraph.graphml"), 'r') as f:
        network = str(f.read())

    jsonNetwork = clean_network(network)
    out_json = open(os.path.join(output,"subgraph.json"), 'w') #Format for Cytoscape.js
    out_json.write(jsonNetwork)
    out_json.close()
    
    return jsonNetwork

def highlight_cluster(query, cluster):
    """Colour assigned cluster in Microreact output"""
    query = stringify(str(query))
    if str(cluster) == query:
        colour = "red"
    else:
        colour = "blue"
    return colour

def api(query, ref_db):
    """Post cluster and tree information to microreact"""
    query = 63
    url = "https://microreact.org/api/project/"
    microreactDF = pd.read_csv(os.path.join(ref_db, ref_db + "_microreact_clusters.csv"))
    microreactDF["Cluster"] = microreactDF['Cluster_Cluster__autocolour']
    microreactDF["CC__colour"] = microreactDF.apply(lambda row: highlight_cluster(query, row["Cluster_Cluster__autocolour"]), axis = 1)
    microreactDF = microreactDF.drop(columns=['Cluster_Cluster__autocolour'])
    clusters = microreactDF.to_csv()

    with open("GPS_v3_references/GPS_v3_references.nwk", "r") as nwk:
        tree = nwk.read()

    description = "A tree representing all samples in the reference database, excluding the query sequence but highlighting its assigned cluster. The cluster assigned to the query is coloured red. If no clusters are highlighted red, query sequence was assigned to a new cluster."
    data = {"name":"PopPUNK-web","description": description,"data":clusters,"tree":tree}
    urlResponse = requests.post(url, data = data)
    microreactResponse = json.loads(urlResponse.text)
    for key, value in microreactResponse.items():
        if key == "url":
            url = value
    return url

def calc_prevalence(cluster, cluster_list, num_samples):
    """Cluster prevalences for Plotly.js"""
    clusterCount = cluster_list.count(cluster)
    prevalence = round(clusterCount / num_samples * 100, 2)
    return prevalence 

def stringify(cluster):
    """Ensure Plotly.js interprets clusters as categorical variables"""
    cluster = '"' + str(cluster) + '"'
    return cluster

def summarise_clusters(output, ref_db):
    """Retreieve assigned query and all cluster prevalences"""
    queryDF = pd.read_csv(os.path.join(output, output + "_clusters.csv"))
    queryDF = queryDF.loc[queryDF['Taxon'] == "query"]
    query = str(queryDF["Cluster"][0])
    queryDQ = stringify(query)
    clusterDF = pd.read_csv(os.path.join(ref_db, ref_db + "_clusters.csv"))
    clusterDF = clusterDF.append(queryDF)
    num_samples = len(clusterDF["Taxon"])
    clusterDF["Cluster"] = clusterDF["Cluster"].apply(stringify)
    cluster_list = list(clusterDF["Cluster"])

    clusterDF["Prevalence"] = clusterDF.apply(lambda row: calc_prevalence(row["Cluster"], cluster_list, num_samples), axis = 1)
    clusterDF = clusterDF.sort_values(by='Prevalence', ascending=False)

    uniqueclusterDF = clusterDF.drop_duplicates(subset=['Cluster'])
    clusters = list(uniqueclusterDF['Cluster'])
    prevalences = list(uniqueclusterDF["Prevalence"])
    query_prevalence = prevalences[clusters.index(queryDQ)]

    return query, query_prevalence, clusters, prevalences
    