"""
Calculate rates with various fitting functions
"""

import copy
import numpy as np
from scipy.special import eval_chebyt
from phydat import phycon

RC = phycon.RC_cal  # gas constant in cal/(mol.K)
RC2 = phycon.RC_atm  # gas constant in cm^3.atm/(mol.K)


def eval_rxn_param_dct(rxn_param_dct, pressures, temps):
    """ Loop through all rxns in a rxn_param_dct and get a ktp_dct for
        each one. Return a rxn_ktp_dct.

        :param rxn_param_dct:
        :type rxn_param_dct:
        :param pressures:
        :type pressures:
        :param temps:
        :type temps:
    """
    def add_ktp_dcts(ktp_dct1, ktp_dct2):
        """ Add the rates in two ktp_dcts. The input dcts should have identical P and T values.
            However, this may not always be true if one is from PLOG and one is from an expression
            with a pressure-independent rate.
        """
        if ktp_dct1 == {}:  # if the starting dct is empty, just copy the addition
            added_dct = copy.deepcopy(ktp_dct2)
        else:
            added_dct = {}
            for pressure, (temps, kts1) in ktp_dct1.items():
                if pressure in ktp_dct2.keys():
                    (_, kts2) = ktp_dct2[pressure]  # unpack ktp_dct2 values
                else:  # if the pressure is not in ktp_dct2
                    kts2 = np.zeros(np.shape(kts1))  # just use an array of zeros
                added_kts = kts1 + kts2
                added_dct[pressure] = (temps, added_kts)  # store the added values

        return added_dct

    check_p_t(pressures, temps)  # enforce formatting rules
    rxn_ktp_dct = {}
    for rxn, param_tups in rxn_param_dct.items():
        ktp_dct = {}
        for param_tup in param_tups:
            new_ktp_dct = eval_param_tup(param_tup, pressures, temps)
            ktp_dct = add_ktp_dcts(ktp_dct, new_ktp_dct)
        rxn_ktp_dct[rxn] = ktp_dct

    return rxn_ktp_dct


def eval_param_tup(param_tup, pressures, temps):
    """ Look through a param_tup and evaluate k(T,P) based on the contents. Return a ktp_dct.

    :param param_tup:
    :type param_tup:
    :param pressures:
    :type pressures:
    :param temps:
    :type temps:
    :return ktp_dct: rate constant as a function of temp and pressure
    :rtype: dct {pressure1: {temps1, kts1}, pressures2: ...}
    """
    if param_tup[3] is not None:  # Chebyshev
        alpha = param_tup[3]['alpha_elm']
        t_limits = param_tup[3]['t_limits']
        p_limits = param_tup[3]['p_limits']
        ktp_dct = chebyshev(alpha, t_limits[0], t_limits[1], p_limits[0], p_limits[1],
                            temps, pressures)

    elif param_tup[4] is not None:  # PLOG
        plog_dct = param_tup[4]
        ktp_dct = plog(plog_dct, temps, pressures, t_ref=1.0)

    elif param_tup[2] is not None:  # Troe
        assert param_tup[0] is not None, (
            'Troe parameters are included, but the high-P parameters are absent'
            )
        assert param_tup[1] is not None, (
            'Troe and high-P parameters are included, but the low-P parameters are absent'
            )
        highp_params = param_tup[0]
        lowp_params = param_tup[1]
        troe_params = param_tup[2]
        alpha = troe_params[0]
        ts3 = troe_params[1]  # T***
        ts1 = troe_params[2]  # T*
        if len(troe_params) == 4:
            ts2 = troe_params[3]  # T**; this one is commonly omitted
        else:
            ts2 = None

        ktp_dct = troe(highp_params, lowp_params, temps, pressures,
            alpha, ts3, ts1, ts2, collid_factor=1.0)

    elif param_tup[1] is not None:  # Lindemann
        assert param_tup[0] is not None, (
            'Low-P parameters are included, but the high-P parameters are absent'
            )
        highp_params = param_tup[0]
        lowp_params = param_tup[1]
        ktp_dct = lindemann(highp_params, lowp_params, temps, pressures, collid_factor=1.0)

    else:  # Arrhenius
        assert param_tup[0] is not None, (
            'The param_tup does not seem to contain any useful information.'
            )
        # This case is unique as the kTP dictionary contains only one key/value pair
        highp_params = param_tup[0]
        kts = arrhenius(highp_params, temps, t_ref=1.0)
        ktp_dct = {}
        ktp_dct['high'] = (temps, kts)

    return ktp_dct


def chebyshev(alpha, tmin, tmax, pmin, pmax, temps, pressures):
    """ Calculates T,P-dependent rate constants [k(T,P)]s using
        a Chebyshev functional expression.

        :param alpha: Chebyshev coefficient matrix
        :type alpha: numpy.ndarray
        :param tmin: minimum temperature Chebyshev model is defined
        :type tmin: float
        :param tmax: maximum temperature Chebyshev model is defined
        :type tmax: float
        :param pmin: minimum pressure Chebyshev model is defined
        :type pmin: float
        :param pmax: maximum pressure Chebyshev model is defined
        :type pmax: float
        :param temps: Temps used to calculate high- and low-k(T)s
        :type temps: numpy.ndarray
        :param pressures: Pressures used to calculate k(T,P)s
        :type pressures: list(float)
        :return ktp_dct: k(T,Ps) at all temps and pressures
        :rtype: dict[pressure: temps]
    """
    kp_dct = {}
    for idx, pressure in enumerate(pressures):
        if np.ndim(temps) == 1:
            kp_dct[pressure] = chebyshev_one_pressure(alpha, tmin, tmax, pmin, pmax, temps,
                                                      pressure)
        else:
            kp_dct[pressure] = chebyshev_one_pressure(alpha, tmin, tmax, pmin, pmax, temps[idx],
                                                      pressure)

    ktp_dct = ktp(kp_dct, temps)

    return ktp_dct


def plog(plog_dct, temps, pressures, t_ref=1.0):
    """ Calculate k(T,P) according to a PLOG  expression

        :param plog_dct: Arrhenius fitting parameters at several pressures
        :type plog_dct: dict[pressure: [Arr_params]]
        :param t_ref: Reference temperature (K)
        :type t_ref: float
        :param temps: Temps used to calculate high- and low-k(T)s
        :type temps: numpy.ndarray
        :param pressures: Pressures used to calculate k(T,P)s
        :type pressures: list(float)
        :return ktp_dct: k(T,Ps) at all temps and pressures
        :rtype: dict[pressure: temps]
    """
    # Set the plog pressures to see if pressure is in range
    plog_pressures = list(plog_dct.keys())

    kp_dct = {}
    for index, pressure in enumerate(pressures):
        if min(plog_pressures) <= pressure <= max(plog_pressures):
            if np.ndim(temps) == 1:
                kp_dct[pressure] = plog_one_pressure(
                    plog_dct, temps, pressure, t_ref)
            else:
                kp_dct[pressure] = plog_one_pressure(
                    plog_dct, temps[index], pressure, t_ref)

    # Create the ktp_dct. Note: the high-P limit is not calculated since this does
    # not exist for PLOG
    ktp_dct = ktp(kp_dct, temps)

    return ktp_dct


def troe(highp_params, lowp_params, temps, pressures,
         alpha, ts3, ts1, ts2=None, collid_factor=1.0, t_ref=1.0):
    """ Calculates T,P-dependent rate constants [k(T,P)]s using
        a Troe functional expression.

        :param highp_ks: k(T)s determined at high-pressure
        :type highp_ks: numpy.ndarray
        :param lowp_ks: k(T)s determined at low-pressure
        :type lowp_ks: numpy.ndarray
        :param temps: Temps used to calculate high- and low-k(T)s
        :type temps: numpy.ndarray
        :param pressures: Pressures used to calculate k(T,P)s
        :type pressure: list(float)
        :param alpha: Troe alpha parameter
        :type alpha: float
        :param ts3: Troe T3 parameter
        :type ts3: float
        :param ts1: Troe T1 parameter
        :type ts1: float
        :param ts2: Troe T2 parameter
        :type ts2: float
        :param collid_factor: Buffer enhancement collision factor
        :type collid_factor: float
        :return ktp_dct: k(T,Ps) at all temps and pressures
        :rtype: dict[pressure: temps]
    """
    kp_dct = {}
    for index, pressure in enumerate(pressures):
        if np.ndim(temps) == 1:
            highp_kts = arrhenius(highp_params, temps, t_ref)
            lowp_kts = arrhenius(lowp_params, temps, t_ref)
            kp_dct[pressure] = troe_one_pressure(
                highp_kts, lowp_kts, temps, pressure,
                alpha, ts3, ts1, ts2, collid_factor=collid_factor)
        else:
            highp_kts = arrhenius(highp_params, temps[index], t_ref)
            lowp_kts = arrhenius(lowp_params, temps[index], t_ref)
            kp_dct[pressure] = troe_one_pressure(
                highp_kts, lowp_kts, temps[index], pressure,
                alpha, ts3, ts1, ts2, collid_factor=collid_factor)

    # Create the ktp_dct; this will also add the high-P limit
    ktp_dct = ktp(kp_dct, temps, highp_params)

    return ktp_dct


def lindemann(highp_params, lowp_params, temps, pressures, collid_factor=1.0, t_ref=1.0):
    """ Calculates T,P-dependent rate constants [k(T,P)]s using
        a Lindemann functional expression.

        :param highp_ks: k(T)s determined at high-pressure
        :type highp_ks: numpy.ndarray
        :param lowp_ks: k(T)s determined at low-pressure
        :type lowp_ks: numpy.ndarray
        :param temps: Temps used to calculate high- and low-k(T)s
        :type temps: numpy.ndarray
        :param pressures: Pressures used to calculate k(T,P)s
        :type pressures: list(float)
        :param collid_factor: Buffer enhancement collision factor
        :type collid_factor: float
        :return ktp_dct: k(T,Ps) at all temps and pressures
        :rtype: dict[pressure: temps]
    """
    kp_dct = {}
    for index, pressure in enumerate(pressures):
        if np.ndim(temps) == 1:
            highp_kts = arrhenius(highp_params, temps, t_ref)
            lowp_kts = arrhenius(lowp_params, temps, t_ref)
            kp_dct[pressure] = lindemann_one_pressure(
                highp_kts, lowp_kts, temps, pressure, collid_factor=collid_factor)
        else:
            highp_kts = arrhenius(highp_params, temps[index], t_ref)
            lowp_kts = arrhenius(lowp_params, temps[index], t_ref)
            kp_dct[pressure] = lindemann_one_pressure(
                highp_kts, lowp_kts, temps[index], pressure, collid_factor=collid_factor)

    # Create the ktp_dct; this will also add the high-P limit
    ktp_dct = ktp(kp_dct, temps, highp_params)

    return ktp_dct


def arrhenius(params, temps, t_ref=1.0, rval=RC):
    """ Calculate T-dependent rate constants [k(T)]s using a single Arrhenius expression.

        :param params: Arrhenius parameters
        :type params: list(float); [A, n, Ea]
        :param temps: List of temperatures (K)
        :type temps: numpy.ndarray
        :param t_ref: Reference temperature (K)
        :type t_ref: float
        :return kts: T-dependent rate constants
        :rtype: numpy.ndarray
    """
    assert len(params) == 3, (
        f'Number of parameters is {len(params)}, but it should be 3'
        )

    a_par = params[0]  # pre-exponential A factor (molar basis)
    n_par = params[1]  # temperature exponent
    ea_par = params[2]  # activation energy (cal/mol)

    kts = a_par * ((temps / t_ref)**n_par) * np.exp(-ea_par/(rval*temps))

    return kts


##################### SECTION 3 OF 4: SECONDARY RATE CONSTANT FUNCTIONS #####################

# These functions support the primary functions in Section 2.


def chebyshev_one_pressure(alpha, tmin, tmax, pmin, pmax, temps, pressure):
    """ Calculates T,P-dependent rate constants [k(T,P)]s using
        a Chebyshev functional expression, at a given pressure,
        across several temperatures.

        :param alpha: Chebyshev coefficient matrix
        :type alpha: numpy.ndarray
        :param tmin: minimum temperature Chebyshev model is defined
        :type tmin: float
        :param tmax: maximum temperature Chebyshev model is defined
        :type tmax: float
        :param pmin: minimum pressure Chebyshev model is defined
        :type pmin: float
        :param pmax: maximum pressure Chebyshev model is defined
        :type pmax: float
        :param temps: Temps used to calculate high- and low-k(T)s
        :type temps: numpy.ndarray
        :param pressure: Pressure used to calculate k(T,P)s
        :type pressure: float
        :return ktps: Set of k(T,P)s at given pressure
        :rtype numpy.ndarray
    """

    alpha_nrows, alpha_ncols = alpha.shape

    ktps = np.zeros(len(temps))
    for i, temp in enumerate(temps):
        ctemp = (
            (2.0 * 1/temp - 1/tmin - 1/tmax) /
            (1/tmax - 1/tmin)
        )
        cpress = (
            (2.0 * np.log10(pressure) - np.log10(pmin) - np.log10(pmax)) /
            (np.log10(pmax) - np.log10(pmin))
        )

        logktp = 0.0
        for j in range(alpha_nrows):
            for k in range(alpha_ncols):
                logktp += (
                    alpha[j][k] *
                    eval_chebyt(j, ctemp) *
                    eval_chebyt(k, cpress)
                )

        ktps[i] = 10**(logktp)

    return ktps


def plog_one_pressure(plog_dct, temps, pressure, t_ref):
    """ Calculates T,P-dependent rate constants [k(T,P)]s using
        a PLOG functional expression, at a given pressure,
        across several temperatures.

        :param plog_dct: Arrhenius fitting parameters at several pressures
        :type plog_dct: dict[pressure: [fit_params]]
        :param temps: Temps used to calculate high- and low-k(T)s
        :type temps: numpy.ndarray
        :param pressure: Pressure used to calculate k(T,P)s
        :type pressure: float
        :param t_ref: Reference temperature (K)
        :type t_ref: float
        :return ktps: Set of k(T,P)s at given pressure
        :rtype numpy.ndarray
    """
    plog_pressures = list(plog_dct.keys())

    # Check if pressure is in plog dct; use plog pressure for numerical stab
    pressure_defined = False
    for plog_pressure in plog_pressures:
        if np.isclose(pressure, plog_pressure, atol=1.0e-3):
            pressure_defined = True
            plog_params = plog_dct[plog_pressure]

    # If pressure equals value use arrhenius expression
    if pressure_defined:
        ktps = arrhenius(plog_params, temps, t_ref)

    # Otherwise, interpolate
    else:
        # Use two PLOG pressures our pressure of interest sits between
        for i, _ in enumerate(plog_pressures):
            if i != len(plog_pressures)-1:
                if plog_pressures[i] < pressure < plog_pressures[i+1]:
                    plow = plog_pressures[i]
                    phigh = plog_pressures[i+1]
                    plow_params = plog_dct[plow]
                    phigh_params = plog_dct[phigh]
                    break
        pres_term = (
            (np.log10(pressure) - np.log10(plow)) /
            (np.log10(phigh) - np.log10(plow))
        )

        # Calculate k(T)s at high-P and low-P with Arrhenius expressions
        kt_low = arrhenius(plow_params, temps, t_ref)
        kt_high = arrhenius(phigh_params, temps, t_ref)

        # Calculate K(T,P)s with PLOG expression
        logkt = (
            np.log10(kt_low) +
            ((np.log10(kt_high) - np.log10(kt_low)) * pres_term)
        )
        ktps = 10**(logkt)

    return ktps


def troe_one_pressure(highp_kts, lowp_kts, temps, pressure,
                      alpha, ts3, ts1, ts2=None, collid_factor=1.0):
    """ Calculates T,P-dependent rate constants [k(T,P)]s using
        a Troe functional expression, at a given pressure,
        across several temperatures.

        :param highp_ks: k(T)s determined at high-pressure
        :type highp_ks: numpy.ndarray
        :param lowp_ks: k(T)s determined at low-pressure
        :type lowp_ks: numpy.ndarray
        :param temps: Temps used to calculate high- and low-k(T)s
        :type temps: numpy.ndarray
        :param pressures: Pressure used to calculate k(T,P)s
        :type pressure: float
        :param alpha: Troe alpha parameter
        :type alpha: float
        :param ts3: Troe T3 parameter
        :type ts3: float
        :param ts1: Troe T1 parameter
        :type ts1: float
        :param ts2: Troe T2 parameter
        :type ts2: float
        :param collid_factor: Buffer enhancement collision factor
        :type collid_factor: float
        :return ktps: Set of k(T,P)s at given pressure
        :rtype numpy.ndarray
        :return ktps: Set of k(T,P)s at given pressure
        :rtype: dict[pressure: temps]
    """
    pr_term = _pr_term(highp_kts, lowp_kts, temps, pressure, collid_factor)
    f_term = _f_broadening_term(pr_term, alpha, ts3, ts1, ts2, temps)
    ktps = highp_kts * (pr_term / (1.0 + pr_term)) * f_term

    return ktps


def lindemann_one_pressure(highp_kts, lowp_kts, temps, pressure,
                           collid_factor=1.0):
    """ Calculates T,P-dependent rate constants [k(T,P)]s using
        a Lindemann functional expression, at a given pressure,
        across several temperatures.

        :param highp_kts: k(T)s determined at high-pressure
        :type highp_kts: numpy.ndarray
        :param lowp_kts: k(T)s determined at low-pressure
        :type lowp_kts: numpy.ndarray
        :param temps: Temps used to calculate high- and low-k(T)s
        :type temps: numpy.ndarray
        :param pressure: Pressure used to calculate k(T,P)s
        :type pressure: float
        :param collid_factor: Buffer enhancement collision factor
        :type collid_factor: float
        :return ktps: Set of k(T,P)s at given pressure
        :rtype numpy.ndarray
    """
    pr_terms = _pr_term(highp_kts, lowp_kts, temps, pressure,
                        collid_factor=collid_factor)
    ktps = highp_kts * (pr_terms / (1.0 + pr_terms))

    return ktps


def _pr_term(highp_rateks, lowp_rateks, temps, pressure, collid_factor=1.0, rval=RC2):
    """ Calculates the reduced pressure term for a single pressure
        used for Lindemann and Troe P-dependent functional expressions.

        :param list highp_ks: k(T)s determined at high-pressure
        :type highp_ks: numpy.ndarray
        :param list lowp_ks: k(T)s determined at low-pressure
        :type lowp_ks: numpy.ndarray
        :param temps: Temps used to calculate high- and low-k(T)s
        :temps: numpy.ndarray
        :param pressure: Pressure used to calculate reduced pressure
        :type pressure: float
        :rtype: numpy.ndarray
    """
    pr_term = (
        (lowp_rateks / highp_rateks) *
        p_to_m(pressure, temps, rval=rval) *
        collid_factor
    )

    return pr_term


def _f_broadening_term(pr_term, alpha, ts3, ts1, ts2, temp):
    """ Calculates the F broadening factor term used for Troe expressions.

        :param pr_term: reduced pressure
        :type pr_term: float
        :param alpha: Troe alpha parameter
        :type alpha: float
        :param ts3: Troe T3 parameter
        :type ts3: float
        :param ts1: Troe T1 parameter
        :type ts1: float
        :param ts2: Troe T2 parameter
        :type ts2: float
        :param temp: temperature
        :type temp: float
        :return f_cent: F broadening term
        :rtype: float
    """

    # Calculate Fcent term
    f_cent = ((1.0 - alpha) * np.exp(-temp / ts3) +
              alpha * np.exp(-temp / ts1))
    if ts2 is not None:
        f_cent += np.exp(-ts2 / temp)

    # Calculate the Log F term
    c_val = -0.4 - 0.67 * np.log10(f_cent)
    n_val = 0.75 - 1.27 * np.log10(f_cent)
    d_val = 0.14
    val = ((np.log10(pr_term) + c_val) /
           (n_val - d_val * (np.log10(pr_term) + c_val)))**2
    logf = (1.0 + val)**(-1) * np.log10(f_cent)

    # Calculate F broadening term
    f_term = 10**(logf)

    return f_term


def p_to_m(pressure, temps, rval=RC2):
    """ Convert the pressure to the concentration of a gas [M]
        assuming an ideal gas form where [M] ~ P/RT.

        :param pressure: pressure of gas (in atm)
        :type pressure: float
        :return: concentration of gas (mol/cm^3)
        :rtype: float
    """
    return pressure / (rval * temps)


def ktp(kp_dct, temps, highp_params=None, t_ref=1.0):
    """ Creates a kTP dictionary from a kP dictionary and an
        array of temperatures

        :param kp_dct: dct of the form {P:[k@T1, k@T2]}
        :type kp_dct: {float: np.array}
        :param temps: array of temperatures, either 1- or 2-D
        :type temps: np.ndarray
        :return ktp_dct:
        :rtype:

    """
    ktp_dct = {}
    for index, pressure in enumerate(kp_dct):
        kts = kp_dct[pressure]
        if np.ndim(temps) == 1:  # if the dimensionality of temps is 1
            ktp_dct[pressure] = (temps,kts)
        else:  # if the dimensionality of temps is 2
            ktp_dct[pressure] = (temps[index],kts)

    # Add the high-P k(T)s to the kTP dictionary if needed
    if highp_params:
        if np.ndim(temps) == 1:
            highp_kts = arrhenius(highp_params, temps, t_ref)
            ktp_dct['high'] = (temps,highp_kts)
        else:
            highp_kts = arrhenius(highp_params, temps[-1], t_ref)  # use the last value in temps
            ktp_dct['high'] = (temps[-1],highp_kts)

    return ktp_dct


def check_p_t(pressures, temps):
    """ Enforces certain rules regarding the pressure and temperature arrays.

        :param pressures: array of pressures
        :type pressures: numpy.ndarray
        :param temps: array of temps
        :type temps: numpy.ndarray
    """
    # Check that the dimensionality of the temps array is either 1 or 2
    temp_dim = np.ndim(temps)
    assert temp_dim in (1,2), (
        f'The dimensionality of temps is {temp_dim}; it should be either 1 or 2'
        )

    # If temps is 2-D, enforce that the # of values in each temp array matches the # of pressures
    if temp_dim == 2:
        len_temps = np.shape(temps)[1]
        len_pressures = len(pressures)
        assert len_pressures ==  len_temps,(
            f'# of pressures is {len_pressures}, while # of temps in each array is {len_temps}'
            )
