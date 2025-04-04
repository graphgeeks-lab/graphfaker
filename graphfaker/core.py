import networkx as nx
import random
from faker import Faker

class GraphFaker:
    def __init__(self):
        self.graph = nx.DiGraph()
        self.faker = Faker()

    def add_person(self):
        person_id = f"person_{len(self.graph.nodes)}"
        self.graph.add_node(person_id, type="Person", name=self.faker.name(), age=random.randint(18, 80))
        return person_id

    def add_organization(self):
        org_id = f"org_{len(self.graph.nodes)}"
        self.graph.add_node(org_id, type="Organization", name=self.faker.company())
        return org_id

    def add_address(self):
        addr_id = f"addr_{len(self.graph.nodes)}"
        self.graph.add_node(addr_id, type="Address", location=self.faker.address())
        return addr_id

    def create_relationships(self, num=10):
        nodes = list(self.graph.nodes)
        for _ in range(num):
            n1, n2 = random.sample(nodes, 2)
            if self.graph.nodes[n1]['type'] == "Person" and self.graph.nodes[n2]['type'] == "Organization":
                self.graph.add_edge(n1, n2, relationship="works_at")
            elif self.graph.nodes[n1]['type'] == "Person" and self.graph.nodes[n2]['type'] == "Address":
                self.graph.add_edge(n1, n2, relationship="lives_at")

    def filter_nodes(self, node_type):
        return [n for n, attr in self.graph.nodes(data=True) if attr['type'] == node_type]

    def filter_edges(self, relationship):
        return [(u, v) for u, v, attr in self.graph.edges(data=True) if attr['relationship'] == relationship]

# Example Usage
if __name__ == "__main__":
    gf = GraphFaker()
    for _ in range(5):
        gf.add_person()
        gf.add_organization()
        gf.add_address()
    gf.create_relationships(10)
    
    print("People:", gf.filter_nodes("Person"))
    print("Organizations:", gf.filter_nodes("Organization"))
    print("Lives At relationships:", gf.filter_edges("lives_at"))
