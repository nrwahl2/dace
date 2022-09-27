import copy
import math
import numpy as np
import itertools

from dace import SDFG, data, nodes, ScheduleType

import dace.optimization.superoptimization.utils as utils
from dace.transformation.dataflow.strip_mining import StripMining

import dace.transformation.helpers as xfh
from dace.transformation.dataflow import (MapDimShuffle, MapExpansion, MapCollapse, MapTiling, InLocalStorage,
                                          OutLocalStorage, MapSchedule, Vectorization, AccumulateTransient)


def map_schedule_enumerator(cutout: SDFG, state=None) -> SDFG:
    if state is None:
        state = [0, 0, 0, 0, 0]

    in_arrays, out_arrays = _arrays(cutout)
    params = utils.map_params(cutout)

    permutations = list(_permutations(params))
    for i, perm in enumerate(permutations[state[0]:]):
        tilings = list(_tilings(perm))
        for j, tiling in enumerate(tilings[state[1]:]):
            cutout_ = copy.deepcopy(cutout)
            if not _apply_permutation(cutout_, perm):
                continue
            if not _apply_tiling(cutout_, tiling):
                continue
            if not _expand_all_maps(cutout_):
                continue

            expanded_params = utils.map_params(cutout_)

            local_storages = list(_local_storage(expanded_params, in_arrays, out_arrays))
            for k, local_storage in enumerate(local_storages[state[2]:]):
                cutout_expanded = copy.deepcopy(cutout_)
                if not _apply_local_storage(cutout_expanded, local_storage):
                    continue
                if not _collapse_all_maps(cutout_expanded):
                    continue

                collapsed_params = utils.map_params(cutout_expanded)

                parallelizations = list(_parallelizations(collapsed_params))
                for l, parallelization in enumerate(parallelizations[state[3]:]):
                    cutout_par = copy.deepcopy(cutout_expanded)
                    if not _apply_parallelization(cutout_par, parallelization):
                        continue

                    vectorizations = list(_vectorizations())
                    for m, vec_len in enumerate(vectorizations[state[4]:]):
                        cutout_vec = copy.deepcopy(cutout_par)
                        if not _apply_vectorization(cutout_vec, vec_len):
                            continue

                        try:
                            cutout_vec.validate()

                            state_ = np.add((i, j, k, l, m), state).tolist()
                            print(state_)
                            state_desc = f"{perm}#{tiling}#{local_storage}#{parallelization}#{vec_len}"
                            yield cutout_vec, state_, state_desc
                        except:
                            continue


def _permutations(all_params):
    perms = [list(itertools.permutations(level)) for level in all_params]
    perms = itertools.product(*perms)
    for perm in perms:
        yield perm

def _tilings(all_params, tile_sizes_range=None):
    if tile_sizes_range is None:
        tile_sizes_range = range(0, 9)

    # Go over all square tilings, tiling with each dimension.
    tile_sizes = [2**k for k in tile_sizes_range]
    tilings = itertools.product(tile_sizes, repeat=len(all_params))
    for tiling in tilings:
        strategy = {}
        for i, group in enumerate(all_params):
            for param in group:
                strategy[param] = tiling[i]

        yield strategy

def _local_storage(all_params, in_arrays, out_arrays):
    arrays = list(in_arrays)
    arrays.extend(out_arrays)

    op = {"in": {}, "out": {}}
    if len(all_params) <= 1 or len(arrays) == 0:
        yield op
        return

    options = []
    for array in arrays:
        array_options = []
        zeros = [0] * (len(all_params) - 1)
        array_options.append(zeros)

        for i in range(len(all_params) - 1):
            bin = [0] * (len(all_params) - 1)
            bin[i] = 1
            array_options.append(bin)

        options.append(array_options)

    options = itertools.product(*options)

    for option in options:
        op = {"in": {}, "out": {}}
        for i in range(len(arrays)):
            array = arrays[i]
            if i < len(in_arrays):
                op["in"][array] = option[i]
            else:
                op["out"][array] = option[i]

        yield op


def _parallelizations(all_params):
    strategies = []
    strategies.append([0] * len(all_params))
    for i, group in enumerate(all_params):
        for j in range(1, len(group) + 1):
            par = [0] * len(all_params)
            par[i] = j
            strategies.append(par)

    for strategy in strategies:
        yield strategy


def _vectorizations():
    for vec_len in [1, 2, 4, 8, 16]:
        yield vec_len


def _apply_permutation(map: SDFG, permutation):
    levels = utils.map_levels(map)

    i = 0
    map_entry = None
    while map_entry in levels:
        map_entry = levels[map_entry]

        MapDimShuffle.apply_to(sdfg=map,
                               map_entry=map_entry,
                               options={"parameters": list(permutation[i])},
                               annotate=False,
                               save=True,
                               verify=False)
        i = i + 1

    return True

def _apply_tiling(map: SDFG, tiling):
    levels = utils.map_levels(map)

    map_entry = None
    while map_entry in levels:
        map_entry = levels[map_entry]

        tile_sizes = [tiling[param] for param in map_entry.map.params]
        if max(tile_sizes) == 1:
            continue

        MapTiling.apply_to(sdfg=map,
                            options={
                                "tile_sizes": tile_sizes,
                                "tile_trivial": False
                            },
                            map_entry=map_entry,
                            save=True,
                            verify=False)

    return True


def _apply_local_storage(map: SDFG, local_storage):
    levels = utils.map_levels(map)

    levels_flat = []
    map_entry = None
    while map_entry in levels:
        map_entry = levels[map_entry]
        levels_flat.append(map_entry)

    in_local_storage = local_storage["in"]
    for array in in_local_storage:
        desc = in_local_storage[array]
        for i, flag in enumerate(desc):
            if flag == 0:
                continue

            outer_map_entry = levels_flat[i]
            inner_map_entry = levels_flat[i + 1]

            InLocalStorage.apply_to(
                sdfg=map,
                node_a=outer_map_entry,
                node_b=inner_map_entry,
                options={"array": array},
                save=True,
                verify=False,
            )

    out_local_storage = local_storage["out"]
    for array in out_local_storage:
        desc = out_local_storage[array]
        for i, flag in enumerate(desc):
            if flag == 0:
                continue

            outer_map_exit = map.start_state.exit_node(levels_flat[i])
            inner_map_exit = map.start_state.exit_node(levels_flat[i + 1])

            xform = OutLocalStorage()
            xform._sdfg = map
            xform.state_id = map.node_id(map.start_state)
            xform.node_a = inner_map_exit
            xform.node_b = outer_map_exit
            xform.array = array
            if xform.can_be_applied(map.start_state, sdfg=map, expr_index=0):
                OutLocalStorage.apply_to(
                    sdfg=map,
                    node_a=inner_map_exit,
                    node_b=outer_map_exit,
                    options={"array": array},
                    save=True,
                    verify=False,
                )
            else:
                xform = AccumulateTransient()
                xform._sdfg = map
                xform.state_id = map.node_id(map.start_state)
                xform.map_exit = inner_map_exit
                xform.outer_map_exit = outer_map_exit
                xform.array = array
                if xform.can_be_applied(map.start_state, sdfg=map, expr_index=0):
                    AccumulateTransient.apply_to(sdfg=map,
                                                 map_exit=inner_map_exit,
                                                 outer_map_exit=outer_map_exit,
                                                 options={"array": array},
                                                 save=True,
                                                 verify=False)
                else:
                    return False

    return True


def _apply_parallelization(map: SDFG, parallelization):
    levels = utils.map_levels(map)

    map_entry = None
    i = 0
    while map_entry in levels:
        map_entry = levels[map_entry]

        strategy = parallelization[i]
        if strategy == 0:
            schedule_type = ScheduleType.Sequential
            collapse = 1
        else:
            schedule_type = ScheduleType.CPU_Multicore
            collapse = strategy

        MapSchedule.apply_to(sdfg=map,
                             map_entry=map_entry,
                             options={
                                 "schedule_type": schedule_type,
                                 "collapse": collapse
                             },
                             annotate=False,
                             save=True,
                             verify=False)
        i = i + 1
    
    return True


def _apply_vectorization(map: SDFG, vector_len: int):
    if vector_len == 1:
        return True

    levels = utils.map_levels(map)
    map_entry = None
    while map_entry in levels:
        map_entry = levels[map_entry]

    xform = Vectorization()
    xform._sdfg = map
    xform.state_id = map.node_id(map.start_state)
    xform.map_entry = map_entry
    if xform.can_be_applied(map.start_state, sdfg=map, expr_index=0):
        Vectorization.apply_to(sdfg=map,
                               map_entry=map_entry,
                               options={"vector_len": vector_len},
                               save=True,
                               verify=False)
        return True

    return False


def _expand_all_maps(map: SDFG):
    levels = utils.map_levels(map)
    map_entry = None
    while map_entry in levels:
        map_entry = levels[map_entry]
        if len(map_entry.map.params) > 1:
            MapExpansion.apply_to(sdfg=map, map_entry=map_entry, save=True, verify=False, annotate=False)
    
    return True


def _collapse_all_maps(map: SDFG):
    levels = utils.map_levels(map)

    map_entry = None
    levels_rev = []
    while map_entry in levels:
        map_entry = levels[map_entry]
        levels_rev.append(map_entry)
    levels_rev.reverse()

    inner = levels_rev[0]
    for i in range(1, len(levels_rev)):
        outer = levels_rev[i]

        xform = MapCollapse()
        xform._sdfg = map
        xform.state_id = map.node_id(map.start_state)
        xform.outer_map_entry = outer
        xform.inner_map_entry = inner

        if xform.can_be_applied(map.start_state, sdfg=map, expr_index=0):
            inner, _ = MapCollapse.apply_to(sdfg=map,
                                            outer_map_entry=outer,
                                            inner_map_entry=inner,
                                            annotate=False,
                                            save=True,
                                            verify=False)
        else:
            inner = outer

    return True


def _arrays(map: SDFG):
    parent_map_entry = None
    for node in map.start_state.nodes():
        if (not isinstance(node, nodes.MapEntry) or not xfh.get_parent_map(map.start_state, node) is None):
            continue

        parent_map_entry = node
        break

    in_arrays = set()
    for edge in map.start_state.in_edges(parent_map_entry):
        if not isinstance(edge.src, nodes.AccessNode):
            continue

        in_array = map.arrays[edge.data.data]
        if isinstance(in_array, data.Scalar):
            continue

        in_arrays.add(edge.data.data)

    parent_map_exit = map.start_state.exit_node(parent_map_entry)
    out_arrays = set()
    for edge in map.start_state.out_edges(parent_map_exit):
        if not isinstance(edge.dst, nodes.AccessNode):
            continue

        out_array = map.arrays[edge.data.data]
        if isinstance(out_array, data.Scalar):
            continue

        out_arrays.add(edge.data.data)

    return in_arrays, out_arrays
