# Copyright 2019-2022 ETH Zurich and the DaCe authors. All rights reserved.
from numbers import Integral, Number
from typing import Sequence, Union

import dace
from dace import dtypes, symbolic
from dace.frontend.common import op_repository as oprepo
from dace.memlet import Memlet
from dace.sdfg import SDFG, SDFGState

# ShapeType = Sequence[Union[Integral, str, symbolic.symbol, symbolic.SymExpr, symbolic.sympy.Basic]]
# RankType = Union[Integral, str, symbolic.symbol, symbolic.SymExpr, symbolic.sympy.Basic]


# Numpy replacement
@oprepo.replaces('dace.csrmm')
@oprepo.replaces('dace.CSRMM')
def csrmm_libnode(pv: 'ProgramVisitor',
                  sdfg: SDFG,
                  state: SDFGState,
                  A_rows,
                  A_cols,
                  A_vals,
                  B,
                  C,
                  alpha,
                  beta,
                  op_a=0):
    # Add nodes
    A_rows_in, A_cols_in, A_vals_in, B_in = (state.add_access(name) for name in (A_rows, A_cols, A_vals, B))
    C_out = state.add_write(C)

    from dace.libraries.sparse import CSRMM
    libnode = CSRMM('csrmm', opA=op_a, alpha=alpha, beta=beta)
    state.add_node(libnode)

    # Connect nodes
    state.add_edge(A_rows_in, None, libnode, '_a_rows', Memlet(A_rows))
    state.add_edge(A_cols_in, None, libnode, '_a_cols', Memlet(A_cols))
    state.add_edge(A_vals_in, None, libnode, '_a_vals', Memlet(A_vals))
    state.add_edge(B_in, None, libnode, '_b', Memlet(B))
    state.add_edge(libnode, '_c', C_out, None, Memlet(C))

    if beta != 0:
        C_in = state.add_read(C)
        state.add_edge(C_in, None, libnode, '_cin', Memlet(C))

    return []
