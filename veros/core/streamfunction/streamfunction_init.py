from veros import logger
from veros.core.operators import numpy as np

from veros import (
    veros_kernel, veros_routine, KernelOutput,
    runtime_settings as rs, runtime_state as rst
)
from veros.variables import allocate
from veros.distributed import global_max
from veros.core import utilities as mainutils
from veros.core.operators import update, at
from veros.core.streamfunction import island, line_integrals
from veros.core.streamfunction.solvers import get_linear_solver


@veros_routine
def get_isleperim(state):
    """
    preprocess land map using MOMs algorithm for B-grid to determine number of islands
    """
    from veros.state import _resize_dimension
    vs = state.variables
    settings = state.settings

    logger.debug(' Determining number of land masses')
    land_map = island.isleperim(vs.kbot, settings.enable_cyclic_x)
    logger.debug(_ascii_map(land_map.copy()))

    nisle = int(global_max(np.max(land_map)))
    _resize_dimension(state, "isle", nisle)

    vs.land_map = land_map


@veros_routine
def streamfunction_init(state):
    """
    prepare for island integrals
    """
    vs = state.variables
    settings = state.settings

    logger.info('Initializing streamfunction method')

    get_isleperim(state)

    boundary_masks_out = boundary_masks(state)
    vs.update(boundary_masks_out)

    # populate linear solver cache
    linear_solver = get_linear_solver(state)

    """
    precalculate time independent boundary components of streamfunction
    """
    forc = allocate(state.dimensions, ("xt", "yt"))

    vs.psin = update(vs.psin, at[...], vs.maskZ[..., -1, np.newaxis])

    for isle in range(state.dimensions["isle"]):
        logger.info(' Solving for boundary contribution by island {:d}'.format(isle))
        isle_sol = linear_solver.solve(state, forc, vs.psin[:, :, isle], boundary_val=vs.boundary_mask[:, :, isle])
        vs.psin = update(vs.psin, at[:, :, isle], isle_sol)

    vs.psin = mainutils.enforce_boundaries(vs.psin, settings.enable_cyclic_x)

    line_psin_out = island_integrals(state)
    vs.update(line_psin_out)


@veros_kernel
def island_integrals(state):
    """
    precalculate time independent island integrals
    """
    vs = state.variables

    fpx = allocate(state.dimensions, ("xt", "yt", "isle"))
    fpy = allocate(state.dimensions, ("xt", "yt", "isle"))

    fpx = update(fpx, at[1:, 1:, :], -(vs.psin[1:, 1:, :] - vs.psin[1:, :-1, :])
        * vs.maskU[1:, 1:, -1, np.newaxis]
        / vs.dyt[np.newaxis, 1:, np.newaxis] * vs.hur[1:, 1:, np.newaxis])
    fpy = update(fpy, at[1:, 1:, ...], (vs.psin[1:, 1:, :] - vs.psin[:-1, 1:, :]) \
        * vs.maskV[1:, 1:, -1, np.newaxis]
        / (vs.cosu[np.newaxis, 1:, np.newaxis] * vs.dxt[1:, np.newaxis, np.newaxis]) \
        * vs.hvr[1:, 1:, np.newaxis])
    line_psin = line_integrals.line_integrals(
        state, uloc=fpx, vloc=fpy, kind='full'
    )

    return KernelOutput(line_psin=line_psin)


@veros_kernel
def boundary_masks(state):
    """
    now that the number of islands is known we can allocate the rest of the variables
    """
    vs = state.variables
    settings = state.settings

    # TODO: use fori_loop with JAX
    for isle in range(state.dimensions["isle"]):
        boundary_map = vs.land_map == (isle + 1)

        if settings.enable_cyclic_x:
            line_dir_east_mask = update(vs.line_dir_east_mask, at[2:-2, 1:-1, isle], boundary_map[3:-1, 1:-1] & ~boundary_map[3:-1, 2:])
            line_dir_west_mask = update(vs.line_dir_west_mask, at[2:-2, 1:-1, isle], boundary_map[2:-2, 2:] & ~boundary_map[2:-2, 1:-1])
            line_dir_south_mask = update(vs.line_dir_south_mask, at[2:-2, 1:-1, isle], boundary_map[2:-2, 1:-1] & ~boundary_map[3:-1, 1:-1])
            line_dir_north_mask = update(vs.line_dir_north_mask, at[2:-2, 1:-1, isle], boundary_map[3:-1, 2:] & ~boundary_map[2:-2, 2:])
        else:
            line_dir_east_mask = update(vs.line_dir_east_mask, at[1:-1, 1:-1, isle], boundary_map[2:, 1:-1] & ~boundary_map[2:, 2:])
            line_dir_west_mask = update(vs.line_dir_west_mask, at[1:-1, 1:-1, isle], boundary_map[1:-1, 2:] & ~boundary_map[1:-1, 1:-1])
            line_dir_south_mask = update(vs.line_dir_south_mask, at[1:-1, 1:-1, isle], boundary_map[1:-1, 1:-1] & ~boundary_map[2:, 1:-1])
            line_dir_north_mask = update(vs.line_dir_north_mask, at[1:-1, 1:-1, isle], boundary_map[2:, 2:] & ~boundary_map[1:-1, 2:])

        boundary_mask = update(vs.boundary_mask, at[..., isle], (
            line_dir_east_mask[..., isle]
            | line_dir_west_mask[..., isle]
            | line_dir_north_mask[..., isle]
            | line_dir_south_mask[..., isle]
        ))

    return KernelOutput(
        boundary_mask=boundary_mask, line_dir_east_mask=line_dir_east_mask, line_dir_west_mask=line_dir_west_mask,
        line_dir_south_mask=line_dir_south_mask, line_dir_north_mask=line_dir_north_mask,
    )


def _ascii_map(boundary_map):
    def _get_char(c):
        if c == 0:
            return '.'
        if c < 0:
            return '#'
        return str(c % 10)

    nx, ny = boundary_map.shape
    map_string = ''
    linewidth = 100
    iremain = nx
    istart = 0
    map_string += '\n'
    map_string += ' ' * (5 + min(linewidth, nx) // 2 - 13) + 'Land mass and perimeter'
    map_string += '\n'
    for isweep in range(1, nx // linewidth + 2):
        iline = min(iremain, linewidth)
        iremain = iremain - iline
        if iline > 0:
            map_string += '\n'
            map_string += ''.join(['{:5d}'.format(istart + i + 1 - 2) for i in range(1, iline + 1, 5)])
            map_string += '\n'
            for j in range(ny - 1, -1, -1):
                map_string += '{:3d} '.format(j)
                map_string += ''.join([_get_char(boundary_map[istart + i - 2, j]) for i in range(2, iline + 2)])
                map_string += '\n'
            map_string += ''.join(['{:5d}'.format(istart + i + 1 - 2) for i in range(1, iline + 1, 5)])
            map_string += '\n'
            istart = istart + iline
    map_string += '\n'
    return map_string
