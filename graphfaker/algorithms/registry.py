# graphfaker/algorithms/registry.py
from graphfaker.algorithms.ba import barabasi_albert_generator


registry = {
    "barabasi": barabasi_albert_generator,
    # add more as you implement them…
}
