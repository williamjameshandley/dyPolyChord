#!/usr/bin/env python
"""
Functions for loading and processing dynamic runs.
"""
import numpy as np
import nestcheck.analyse_run as ar
import nestcheck.data_processing
import nestcheck.io_utils as iou


def settings_root(likelihood_name, prior_name, ndims, **kwargs):
    """Get a standard string containing information about settings."""
    prior_scale = kwargs.pop('prior_scale')
    dynamic_goal = kwargs.pop('dynamic_goal')
    nlive_const = kwargs.pop('nlive_const')
    nrepeats = kwargs.pop('nrepeats')
    ninit = kwargs.pop('ninit', None)
    dyn_nlive_step = kwargs.pop('dyn_nlive_step', None)
    init_step = kwargs.pop('init_step', None)
    if kwargs:
        raise TypeError('Unexpected **kwargs: {0}'.format(kwargs))
    root = likelihood_name + '_' + prior_name + '_' + str(prior_scale)
    root += '_dg' + str(dynamic_goal)
    if dynamic_goal is not None:
        assert ninit is not None
        assert dyn_nlive_step is not None
        root += '_' + str(ninit) + 'init_' + str(dyn_nlive_step) + 'ds'
        if dynamic_goal != 0:
            assert init_step is not None
            root += '_' + str(init_step) + 'is'
    root += '_' + str(ndims) + 'd'
    root += '_' + str(nlive_const) + 'nlive'
    root += '_' + str(nrepeats) + 'nrepeats'
    return root


def get_dypolychord_data(file_root, n_runs, dynamic_goal, **kwargs):
    """
    Load and process polychord chains
    """
    cache_dir = kwargs.pop('cache_dir', 'cache')
    base_dir = kwargs.pop('base_dir', 'chains')
    load = kwargs.pop('load', False)
    save = kwargs.pop('save', False)
    overwrite_existing = kwargs.pop('overwrite_existing', True)
    if kwargs:
        raise TypeError('Unexpected **kwargs: %r' % kwargs)
    save_name = file_root + '_' + str(n_runs) + 'runs'
    if load:
        try:
            return iou.pickle_load(cache_dir + '/' + save_name)
        except OSError:  # FileNotFoundError is a subclass of OSError
            pass
    data = []
    errors = {}
    # load and process chains
    for i in range(1, n_runs + 1):
        try:
            data.append(process_dypolychord_run(file_root + '_' + str(i),
                                                base_dir, dynamic_goal))
        except (OSError, AssertionError, KeyError) as err:
            try:
                errors[type(err).__name__].append(i)
            except KeyError:
                errors[type(err).__name__] = [i]
    for error_name, val_list in errors.items():
        if val_list:
            save = False  # only save if every file is processed ok
            message = (error_name + ' processing ' + str(len(val_list)) + ' / '
                       + str(n_runs) + ' files')
            if len(val_list) != n_runs:
                message += '. Runs with errors were: ' + str(val_list)
            print(message)
    if save:
        print('Processed new chains: saving to ' + save_name)
        iou.pickle_save(data, cache_dir + '/' + save_name, print_time=False,
                        overwrite_existing=overwrite_existing)
    return data


def process_dypolychord_run(file_root, base_dir, dynamic_goal):
    assert dynamic_goal in [0, 1], (
        'dynamic_goal=' + str(dynamic_goal) + '! '
        'So far only set up for dynamic_goal = 0 or 1')
    init = nestcheck.data_processing.process_polychord_run(
        file_root + '_init', base_dir)
    dyn = nestcheck.data_processing.process_polychord_run(
        file_root + '_dyn', base_dir)
    assert np.all(init['thread_min_max'][:, 0] == -np.inf)
    if dynamic_goal == 0:
        # dyn was not resumed part way through init and we can simply combine
        # dyn and init
        run = ar.combine_ns_runs([init, dyn])
        run['output'] = {'nlike': (init['output']['nlike'] +
                                   dyn['output']['nlike'])}
    elif dynamic_goal == 1:
        # dyn was resumed part way through init and we need to remove duplicate
        # points
        dyn_info = iou.pickle_load(base_dir + '/' + file_root + '_dyn_info')
        run = combine_resumed_dyn_run(init, dyn, dyn_info['resume_ndead'])
        run['output'] = dyn_info
        run['output']['nlike'] = (init['output']['nlike'] +
                                  dyn['output']['nlike'] -
                                  dyn_info['resume_nlike'])
    # check the nested sampling run has the expected properties and resume
    nestcheck.data_processing.check_ns_run(run)
    return run


def combine_resumed_dyn_run(init, dyn, resume_ndead):
    """
    Process dynamic nested sampling run including both initial exploratory run
    and second dynamic run.
    """
    # Remove the first resume_ndead points which are in both dyn and init from
    # init
    assert np.array_equal(init['logl'][:resume_ndead],
                          dyn['logl'][:resume_ndead])
    init['theta'] = init['theta'][resume_ndead:, :]
    for key in ['nlive_array', 'logl', 'thread_labels']:
        init[key] = init[key][resume_ndead:]
    # We also need to remove the points that were live when the resume file was
    # written, as these show up as dead points in dyn
    live_inds = []
    empty_thread_inds = []
    for i, th_lab in enumerate(np.unique(init['thread_labels'])):
        th_inds = np.where(init['thread_labels'] == th_lab)[0]
        live_inds.append(th_inds[0])
        live_logl = init['logl'][th_inds[0]]
        if th_inds.shape[0] == 1:
            empty_thread_inds.append(i)
        assert np.where(dyn['logl'] == live_logl)[0].shape == (1,), \
            'this point should be present in dyn too!'
        init['thread_min_max'][i, 0] = live_logl
    # lets remove the live points at init
    init['theta'] = np.delete(init['theta'], live_inds, axis=0)
    for key in ['nlive_array', 'logl', 'thread_labels']:
        init[key] = np.delete(init[key], live_inds)
    # Deal with the case that one of the threads is now empty
    if empty_thread_inds:
        # remove any empty threads from logl_min_max
        init['thread_min_max'] = np.delete(
            init['thread_min_max'], empty_thread_inds, axis=0)
        # Now we need to reorder the thread labels to avoid gaps
        thread_labels_new = np.full(init['thread_labels'].shape, np.nan)
        for i, th_lab in enumerate(np.unique(init['thread_labels'])):
            inds = np.where(init['thread_labels'] == th_lab)[0]
            thread_labels_new[inds] = i
            # Check the newly relabelled thread label matches thread_min_max
            assert init['thread_min_max'][i, 0] <= init['logl'][inds[0]]
            assert init['thread_min_max'][i, 1] == init['logl'][inds[-1]]
        assert np.all(~np.isnan(thread_labels_new))
        init['thread_labels'] = thread_labels_new.astype(int)
    # Add the init threads to dyn with new labels that continue on from the dyn
    # labels
    init['thread_labels'] += dyn['thread_min_max'].shape[0]
    run = ar.combine_threads(ar.get_run_threads(dyn) +
                             ar.get_run_threads(init),
                             assert_birth_point=True)
    return run