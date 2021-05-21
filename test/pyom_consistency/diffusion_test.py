import pytest

from veros.core import diffusion

from test_base import get_random_state, compare_state


@pytest.fixture
def random_state(pyom2_lib):
    vs_state, pyom_obj = get_random_state(
        pyom2_lib,
        extra_settings=dict(
            nx=70,
            ny=60,
            nz=50,
            dt_tracer=3600,
            dt_mom=3600,
            enable_cyclic_x=True,
            enable_conserve_energy=True,
            enable_hor_friction_cos_scaling=True,
            enable_tempsalt_sources=True,
            K_hbi=1,
            K_h=1,
            hor_friction_cosPower=2,
        ),
    )

    # implementations are only identical if non-water values are 0
    vs = vs_state.variables
    for var in (
        "P_diss_sources",
        "P_diss_hmix",
    ):
        getattr(pyom_obj.main_module, var.lower())[...] = 0.0
        with vs.unlock():
            setattr(vs, var, vs.get(var) * 0.0)

    return vs_state, pyom_obj


def test_tempsalt_biharmonic(random_state):
    vs_state, pyom_obj = random_state
    vs_state.variables.update(diffusion.tempsalt_biharmonic(vs_state))
    pyom_obj.tempsalt_biharmonic()
    compare_state(vs_state, pyom_obj)


def test_tempsalt_diffusion(random_state):
    vs_state, pyom_obj = random_state
    vs_state.variables.update(diffusion.tempsalt_diffusion(vs_state))
    pyom_obj.tempsalt_diffusion()
    compare_state(vs_state, pyom_obj)


def test_tempsalt_sources(random_state):
    vs_state, pyom_obj = random_state
    vs_state.variables.update(diffusion.tempsalt_sources(vs_state))
    pyom_obj.tempsalt_sources()
    compare_state(vs_state, pyom_obj)
