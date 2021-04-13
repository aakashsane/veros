from veros import veros_kernel, veros_routine, KernelOutput
from veros.variables import allocate
from veros.core import density, diffusion, utilities
from veros.core.operators import update, at, numpy as np


# TODO: replace cumsums
@veros_kernel
def u_centered_grid(dyt, dyu, yt, yu):
    yu = update(yu, at[0], 0)
    yu = update(yu, at[1:], np.cumsum(dyt[1:]))

    yt = update(yt, at[0], yu[0] - dyt[0] * 0.5)
    yt = update(yt, at[1:], 2 * yu[:-1])

    alternating_pattern = np.ones_like(yt)
    alternating_pattern = update(alternating_pattern, at[::2], -1)
    yt = update(yt, at[...], alternating_pattern * np.cumsum(alternating_pattern * yt))

    dyu = update(dyu, at[:-1], yt[1:] - yt[:-1])
    dyu = update(dyu, at[-1], 2 * dyt[-1] - dyu[-2])
    return KernelOutput(dyu=dyu, yt=yt, yu=yu)


@veros_kernel
def calc_grid_kernel(state):
    vs = state.variables
    settings = state.settings

    dxt = vs.dxt
    if settings.enable_cyclic_x:
        dxt = update(dxt, at[-2:], dxt[2:4])
        dxt = update(dxt, at[:2], dxt[-4:-2])
    else:
        dxt = update(dxt, at[-2:], dxt[-3])
        dxt = update(dxt, at[:2], dxt[2])

    dyt = vs.dyt
    dyt = update(dyt, at[-2:], dyt[-3])
    dyt = update(dyt, at[:2], dyt[2])

    """
    grid in east/west direction
    """
    dxu, xt, xu = u_centered_grid(dxt, vs.dxu, vs.xt, vs.xu)
    xt = xt + settings.x_origin - xu[2]
    xu = xu + settings.x_origin - xu[2]

    if settings.enable_cyclic_x:
        xt = update(xt, at[-2:], xt[2:4])
        xt = update(xt, at[:2], xt[-4:-2])
        xu = update(xu, at[-2:], xt[2:4])
        xu = update(xu, at[:2], xu[-4:-2])
        dxu = update(dxu, at[-2:], dxu[2:4])
        dxu = update(dxu, at[:2], dxu[-4:-2])

    """
    grid in north/south direction
    """
    dyu, yt, yu = u_centered_grid(dyt, vs.dyu, vs.yt, vs.yu)
    yt = yt + settings.y_origin - yu[2]
    yu = yu + settings.y_origin - yu[2]

    if settings.coord_degree:
        """
        convert from degrees to pseudo cartesian grid
        """
        dxt = dxt * settings.degtom
        dxu = dxu * settings.degtom
        dyt = dyt * settings.degtom
        dyu = dyu * settings.degtom

    """
    grid in vertical direction
    """
    dzw, zt, zw = u_centered_grid(vs.dzt, vs.dzw, vs.zt, vs.zw)
    zt = zt - zw[-1]
    zw = zw - zw[-1]  # enforce 0 boundary height

    """
    metric factors
    """
    if settings.coord_degree:
        cost = update(vs.cost, at[...], np.cos(yt * settings.pi / 180.))
        cosu = update(vs.cosu, at[...], np.cos(yu * settings.pi / 180.))
        tantr = update(vs.tantr, at[...], np.tan(yt * settings.pi / 180.) / settings.radius)
    else:
        cost = update(vs.cost, at[...], 1.0)
        cosu = update(vs.cosu, at[...], 1.0)
        tantr = update(vs.tantr, at[...], 0.0)

    """
    precalculate area of boxes
    """
    area_t = update(vs.area_t, at[...], cost * dyt * dxt[:, np.newaxis])
    area_u = update(vs.area_u, at[...], cost * dyt * dxu[:, np.newaxis])
    area_v = update(vs.area_v, at[...], cosu * dyu * dxt[:, np.newaxis])

    return KernelOutput(
        dxt=dxt, dyt=dyt, dxu=dxu, dyu=dyu,
        xt=xt, yt=yt, xu=xu, yu=yu,
        dzw=dzw, zt=zt, zw=zw,
        cost=cost, cosu=cosu, tantr=tantr,
        area_t=area_t, area_u=area_u, area_v=area_v
    )


@veros_routine
def calc_grid(state):
    """
    setup grid based on dxt,dyt,dzt and x_origin, y_origin
    """
    calc_grid_out = calc_grid_kernel(state)
    state.variables.update(calc_grid_out)


@veros_routine
def calc_beta(state):
    """
    calculate beta = df/dy
    """
    vs = state.variables
    settings = state.settings
    vs.beta = update(vs.beta, at[:, 2:-2], 0.5 * ((vs.coriolis_t[:, 3:-1] - vs.coriolis_t[:, 2:-2]) / vs.dyu[2:-2]
                           + (vs.coriolis_t[:, 2:-2] - vs.coriolis_t[:, 1:-3]) / vs.dyu[1:-3]))
    vs.beta = utilities.enforce_boundaries(vs.beta, settings.enable_cyclic_x)


@veros_kernel
def calc_topo_kernel(state):
    vs = state.variables
    settings = state.settings

    """
    close domain
    """
    kbot = vs.kbot
    kbot = update(kbot, at[:, :2], 0)
    kbot = update(kbot, at[:, -2:], 0)

    kbot = utilities.enforce_boundaries(kbot, settings.enable_cyclic_x)

    if not settings.enable_cyclic_x:
        kbot = update(kbot, at[:2, :], 0)
        kbot = update(kbot, at[-2:, :], 0)

    """
    Land masks
    """
    maskT, maskU, maskV, maskW, maskZ = vs.maskT, vs.maskU, vs.maskV, vs.maskW, vs.maskZ

    land_mask = kbot > 0
    ks = np.arange(maskT.shape[2])[np.newaxis, np.newaxis, :]

    maskT = update(maskT, at[...], land_mask[..., np.newaxis] & (kbot[..., np.newaxis] - 1 <= ks))
    maskT = utilities.enforce_boundaries(maskT, settings.enable_cyclic_x)

    maskU = update(maskU, at[...], maskT)
    maskU = update(maskU, at[:-1, :, :], np.minimum(maskT[:-1, :, :], maskT[1:, :, :]))
    maskU = utilities.enforce_boundaries(maskU, settings.enable_cyclic_x)

    maskV = update(maskV, at[...], maskT)
    maskV = update(maskV, at[:, :-1], np.minimum(maskT[:, :-1], maskT[:, 1:]))
    maskV = utilities.enforce_boundaries(maskV, settings.enable_cyclic_x)

    maskZ = update(maskZ, at[...], maskT)
    maskZ = update(maskZ, at[:-1, :-1], np.minimum(np.minimum(maskT[:-1, :-1], maskT[:-1, 1:]), maskT[1:, :-1]))
    maskZ = utilities.enforce_boundaries(maskZ, settings.enable_cyclic_x)

    maskW = update(maskW, at[...], maskT)
    maskW = update(maskW, at[:, :, :-1], np.minimum(maskT[:, :, :-1], maskT[:, :, 1:]))

    """
    total depth
    """
    ht = np.sum(maskT * vs.dzt[np.newaxis, np.newaxis, :], axis=2)
    hu = np.sum(maskU * vs.dzt[np.newaxis, np.newaxis, :], axis=2)
    hv = np.sum(maskV * vs.dzt[np.newaxis, np.newaxis, :], axis=2)

    hur = np.where(hu != 0, 1 / (hu + 1e-22), 0)
    hvr = np.where(hv != 0, 1 / (hv + 1e-22), 0)

    return KernelOutput(
        maskT=maskT, maskU=maskU, maskV=maskV, maskW=maskW, maskZ=maskZ,
        ht=ht, hu=hu, hv=hv, hur=hur, hvr=hvr, kbot=kbot
    )


@veros_routine
def calc_topo(state):
    """
    calulate masks, total depth etc
    """
    calc_topo_out = calc_topo_kernel(state)
    state.variables.update(calc_topo_out)


@veros_kernel
def calc_initial_conditions_kernel(state):
    vs = state.variables
    settings = state.settings

    temp = utilities.enforce_boundaries(vs.temp, settings.enable_cyclic_x)
    salt = utilities.enforce_boundaries(vs.salt, settings.enable_cyclic_x)

    rho = density.get_rho(state, salt, temp, np.abs(vs.zt)[:, np.newaxis]) * vs.maskT[..., np.newaxis]
    Hd = density.get_dyn_enthalpy(state, salt, temp, np.abs(vs.zt)[:, np.newaxis]) * vs.maskT[..., np.newaxis]
    int_drhodT = update(
        vs.int_drhodT, at[...],
        density.get_int_drhodT(state, salt, temp, np.abs(vs.zt)[:, np.newaxis])
    )
    int_drhodS = update(
        vs.int_drhodS, at[...],
        density.get_int_drhodS(state, salt, temp, np.abs(vs.zt)[:, np.newaxis])
    )

    fxa = -settings.grav / settings.rho_0 / vs.dzw[np.newaxis, np.newaxis, :] * vs.maskW
    Nsqr = update(vs.Nsqr, at[:, :, :-1, :], fxa[:, :, :-1, np.newaxis]
                  * (density.get_rho(state, salt[:, :, 1:, :], temp[:, :, 1:, :], np.abs(vs.zt)[:-1, np.newaxis])
                     - rho[:, :, :-1, :]))
    Nsqr = update(Nsqr, at[:, :, -1, :], Nsqr[:, :, -2, :])

    return KernelOutput(salt=salt, temp=temp, rho=rho, Hd=Hd, int_drhodT=int_drhodT, int_drhodS=int_drhodS, Nsqr=Nsqr)


@veros_routine
def calc_initial_conditions(state):
    """
    calculate dyn. enthalp, etc
    """
    vs = state.variables

    if np.any(vs.salt < 0.0):
        raise RuntimeError('encountered negative salinity')

    initial_conditions_out = calc_initial_conditions_kernel(state)
    vs.update(initial_conditions_out)


@veros_kernel
def ugrid_to_tgrid(state, a):
    vs = state.variables
    b = np.zeros_like(a)
    b = update(b, at[2:-2, :, :], (vs.dxu[2:-2, np.newaxis, np.newaxis] * a[2:-2, :, :]
                     + vs.dxu[1:-3, np.newaxis, np.newaxis] * a[1:-3, :, :]) \
        / (2 * vs.dxt[2:-2, np.newaxis, np.newaxis]))
    return b


@veros_kernel
def vgrid_to_tgrid(state, a):
    vs = state.variables
    b = np.zeros_like(a)
    b = update(b, at[:, 2:-2, :], (vs.area_v[:, 2:-2, np.newaxis] * a[:, 2:-2, :]
                     + vs.area_v[:, 1:-3, np.newaxis] * a[:, 1:-3, :]) \
        / (2 * vs.area_t[:, 2:-2, np.newaxis]))
    return b


@veros_kernel
def calc_diss_u(state, diss):
    vs = state.variables
    ks = allocate(state.dimensions, ("xt", "yt"))
    ks = update(ks, at[1:-2, 2:-2], np.maximum(vs.kbot[1:-2, 2:-2], vs.kbot[2:-1, 2:-2]))
    diss_u = diffusion.dissipation_on_wgrid(state, diss, ks)
    return ugrid_to_tgrid(state, diss_u)


@veros_kernel
def calc_diss_v(state, diss):
    vs = state.variables
    ks = allocate(state.dimensions, ("xt", "yt"))
    ks = update(ks, at[2:-2, 1:-2], np.maximum(vs.kbot[2:-2, 1:-2], vs.kbot[2:-2, 2:-1]))
    diss_v = diffusion.dissipation_on_wgrid(state, diss, ks)
    return vgrid_to_tgrid(state, diss_v)
