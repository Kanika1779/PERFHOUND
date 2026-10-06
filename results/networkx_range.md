### Perfhound: 208 commits between `networkx-3.2` and `networkx-3.3`

Repository: networkx/networkx · mode: real · 195 with a PR · 28.97 s

| # | commit | date | author | PR | title | functions changed |
|---|---|---|---|---|---|---|
| 1 | `b467287` | 2023-10-18 | Jarrod Millman |  | Bump release version | `networkx.<module>` |
| 2 | `692d5c4` | 2023-10-18 | Jarrod Millman | #7029 | Update release process (#7029) |  |
| 3 | `b43c9ce` | 2023-10-19 | Jonas Otto | #7030 | Fix listing of release notes on Releases page (#7030) |  |
| 4 | `88404b9` | 2023-10-19 | Ross Barnowski | #7034 | Fix syntax warning from bad escape sequence. (#7034) |  |
| 5 | `ce237b7` | 2023-10-20 | Jarrod Millman | #7028 | Drop Python 3.9 support (#7028) | `networkx.algorithms.link_analysis.hits_alg._hits_python`, `networkx.algorithms.lowest_common_ancestors.tree_all_pairs_lowest_common_ancestor`, `networkx.conftest.pytest_configure` +17 |
| 6 | `35a5574` | 2023-10-23 | Dan Schult | #7041 | Fix triangles to avoid using `is` to compare nodes (#7041) | `networkx.algorithms.cluster.triangles`, `networkx.algorithms.similarity._simrank_similarity_python` |
| 7 | `746371a` | 2023-10-23 | Jordan Matelsky | #6825 | fix: Explicitly check for None/False in edge_attr during import from n | `networkx.convert_matrix.from_numpy_array` |
| 8 | `46d67fe` | 2023-10-23 | AKSHAYA MADHURI | #7018 | DOC: Read node attributes from pandas dataframe (#7018) |  |
| 9 | `f1e6cad` | 2023-10-23 | Dan Schult | #7042 | fix extendability function name in bipartite.rst (#7042) |  |
| 10 | `6da43e5` | 2023-10-23 | Ross Barnowski | #7048 | Minor doc cleanups to remove doc build warnings (#7048) |  |
| 11 | `8311f1b` | 2023-10-23 | peijenburg |  | Add Tadpole graph (#6999) | `networkx.generators.classic.<module>`, `networkx.generators.classic.tadpole_graph`, `networkx.generators.tests.test_classic.TestGeneratorClassic.test_tadpole_graph_exceptions` +6 |
| 12 | `5844f0c` | 2023-10-23 | Jarrod Millman | #7043 | Add favicon (#7043) | `doc.conf.<module>` |
| 13 | `3c89da6` | 2023-10-24 | peijenburg |  | Remove unused code resistance_distance (#7053) | `networkx.algorithms.distance_measures.resistance_distance` |
| 14 | `23930ec` | 2023-10-25 | Erik Welch | #7056 | Fix error message for `nx.mycielski_graph(0)` (#7056) | `networkx.generators.mycielski.mycielski_graph` |
| 15 | `27127bc` | 2023-10-25 | Erik Welch | #7055 | Fix names of small graphs (#7055) | `networkx.generators.small.cubical_graph`, `networkx.generators.small.tetrahedral_graph` |
| 16 | `1c52720` | 2023-10-25 | Erik Welch | #7057 | Disallow negative number of nodes in `complete_multipartite_graph` (#7 | `networkx.generators.classic.complete_multipartite_graph`, `networkx.generators.tests.test_classic.TestGeneratorClassic.test_complete_multipartite_graph` |
| 17 | `f9170cc` | 2023-10-27 | Dan Schult | #7062 | Improve error messages for misconfigured backend treatment (#7062) | `networkx.conftest.pytest_configure`, `networkx.lazy_imports.DelayedImportErrorModule.__getattr__` |
| 18 | `a246d78` | 2023-10-28 | Jarrod Millman |  | Add 3.2.1 release notes |  |
| 19 | `ed95a32` | 2023-10-28 | Jarrod Millman |  | Fix typo |  |
| 20 | `4f486e6` | 2023-10-28 | Jarrod Millman |  | Fix typo |  |
| 21 | `f193b00` | 2023-10-31 | Ross Barnowski | #7072 | DOC: Add example to generic_bfs_edges to demonstrate the `neighbors` p |  |
| 22 | `439332b` | 2023-10-31 | Ross Barnowski | #7071 | MAINT: Fixup union exception message. (#7071) | `networkx.algorithms.operators.all.union_all` |
| 23 | `9c953e4` | 2023-10-31 | Ross Barnowski | #7049 | MAINT: Minor touchups to tadpole and lollipop graph (#7049) | `networkx.generators.classic.tadpole_graph`, `networkx.generators.tests.test_classic.TestGeneratorClassic.test_lollipop_graph_mixing_input_types`, `networkx.generators.tests.test_classic.TestGeneratorClassic.test_lollipop_graph_right_sizes` +9 |
| 24 | `0b471dd` | 2023-10-31 | Erik Welch | #7074 | Add `@not_implemented_for("directed")` to `number_connected_components | `networkx.algorithms.components.connected.is_connected`, `networkx.algorithms.components.connected.number_connected_components` |
| 25 | `20c6c0d` | 2023-10-31 | peijenburg |  | remove unused code (#7076) |  |
| 26 | `754bae3` | 2023-11-01 | Ross Barnowski | #7060 | DEP: Deprecate the all_triplets one-liner. (#7060) | `networkx.algorithms.triads.all_triplets`, `networkx.conftest.set_warnings`, `networkx.algorithms.tests.test_triads.test_all_triplets_deprecated` |
| 27 | `948fdcd` | 2023-10-31 | Ross Barnowski | #7059 | Minor touchups to the beamsearch module (#7059) | `networkx.algorithms.traversal.beamsearch.<module>`, `networkx.algorithms.traversal.beamsearch.bfs_beam_edges`, `networkx.algorithms.traversal.tests.test_beamsearch.<module>` +2 |
| 28 | `5146f98` | 2023-10-31 | Ross Barnowski | #7058 | Hierarchical clustering layout gallery example (#7058) |  |
| 29 | `6ced50a` | 2023-10-31 | BrunoBaldissera | #6294 | Fixed an error in the documentation of the katz centrality (#6294) |  |
| 30 | `f5e3764` | 2023-11-01 | Erik Welch | #7079 | Fix annoying split strings on same line (#7079) | `networkx.algorithms.bipartite.edgelist.parse_edgelist` |
| 31 | `314adcb` | 2023-11-01 | Erik Welch | #7081 | Update dispatch decorator for `hits` to use `"weight"` edge weight (#7 | `networkx.algorithms.link_analysis.hits_alg.hits` |
| 32 | `7394b61` | 2023-11-02 | Jarrod Millman | #7083 | Remove nbconvert upper pin (revert #6984) (#7083) |  |
| 33 | `d34dd1a` | 2023-11-03 | Ross Barnowski | #7077 | Add a step to CI to check for warnings at import time. (#7077) |  |
| 34 | `d3e06a1` | 2023-11-04 | Anders Rydbirk | #7073 | [A-star] Added expansion pruning via cutoff if cutoff is provided (#70 | `networkx.algorithms.shortest_paths.astar.astar_path`, `networkx.algorithms.shortest_paths.astar.astar_path_length`, `networkx.algorithms.shortest_paths.tests.test_astar.TestAStar.test_astar_admissible_heuristic_with_cutoff` +5 |
| 35 | `166cd70` | 2023-11-04 | Andrew Knyazev | #7025 | Create 3d_rotation_anime.py (#7025) | `doc.conf.<module>`, `examples.3d_drawing.plot_3d_rotation_animation._frame_update`, `examples.3d_drawing.plot_3d_rotation_animation.init` |
| 36 | `1e5933d` | 2023-11-04 | Erik Welch | #7084 | Make HITS raise exceptions consistent with power iterations (#7084) | `networkx.algorithms.link_analysis.hits_alg.hits`, `networkx.algorithms.link_analysis.tests.test_hits.TestHITS.test_hits_not_convergent` |
| 37 | `2ece903` | 2023-11-06 | Neil Botelho | #6973 | Handle edge cases for greedy_modularity_communities (#6973) | `networkx.algorithms.community.modularity_max.greedy_modularity_communities`, `networkx.algorithms.community.tests.test_modularity_max.test_greedy_modularity_communities_corner_cases` |
| 38 | `eb6c4c4` | 2023-11-06 | AKSHAYA MADHURI | #7086 | DOC: Add docstrings to filter view functions   (#7086) | `networkx.classes.filters.show_nodes` |
| 39 | `9aa44be` | 2023-11-06 | AKSHAYA MADHURI | #7075 | DOC: Add docstrings to Filter mapping views (#7075) | `networkx.classes.coreviews.FilterAdjacency`, `networkx.classes.coreviews.FilterAtlas`, `networkx.classes.coreviews.FilterMultiAdjacency` +1 |
| 40 | `4c451f4` | 2023-11-08 | Chiranjeevi Karthik  | #6976 | Added few tests for /generators/duplication.py and /generators/geomet… | `networkx.generators.geometric.navigable_small_world_graph`, `networkx.generators.tests.test_duplication.TestDuplicationDivergenceGraph.test_final_size`, `networkx.generators.tests.test_duplication.TestDuplicationDivergenceGraph.test_probability_too_large` +8 |
| 41 | `e7e48e4` | 2023-11-08 | Jarrod Millman | #7096 | Test on Python 3.13-dev (#7096) |  |
| 42 | `a8b5d06` | 2023-11-09 | Ross Barnowski | #7061 | DEP: Deprecate random_triad (#7061) | `networkx.algorithms.triads.random_triad`, `networkx.conftest.set_warnings`, `networkx.algorithms.tests.test_triads.test_random_triad_deprecated` |
| 43 | `d5d28d6` | 2023-11-08 | Mridul Seth | #6706 | DOCS: Fix internal links to other functions in isomorphvf2 (#6706) |  |
| 44 | `6b3f7bb` | 2023-11-08 | Mridul Seth | #7092 | FIX: Match the doc description while copying over data (#7092) | `networkx.algorithms.operators.binary.difference`, `networkx.algorithms.operators.binary.symmetric_difference`, `networkx.algorithms.operators.tests.test_binary.test_difference_attributes` |
| 45 | `e962608` | 2023-11-08 | Aditi Juneja | #6995 | added note for the triangle inequality case in TSP (#6995) |  |
| 46 | `0f5c2c4` | 2023-11-10 | Ross Barnowski | #7103 | Add note about importance of testing to contributor guide (#7103) |  |
| 47 | `9c97500` | 2023-11-11 | Ross Barnowski | #7104 | Proposal to add centrality overview to mentored projects. (#7104) |  |
| 48 | `e3d5146` | 2023-11-11 | Dilara Tekinoglu | #5473 | Improve documentation of Component Algorithms (#5473) |  |
| 49 | `6b0385d` | 2023-11-11 | Patrick Nicodemus | #6261 | Changed arguments list of GraphMLWriterLxml.dump() (#6261) | `networkx.readwrite.graphml.GraphMLWriterLxml.dump` |
| 50 | `312bb3a` | 2023-11-12 | Ross Barnowski | #5061 | Add dot io to readwrite (#5061) |  |

... and 158 more commits (see the JSON file).
