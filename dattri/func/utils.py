"""Utility functions for working with models and parameters."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any, Dict, Optional

import functools

import torch
from torch import Tensor


def _vectorize(g: Dict[str, torch.Tensor],
               batch_dim: Optional[bool] = True,
               arr: Optional[torch.Tensor] = None,
               device: Optional[str] = "cuda") -> Tensor:
    """Vectorize gradient result (g) into arr.

    This function takes a dictionary of gradient and returns a flattened tensor
    of shape [batch_size, num_params].

    Args:
        g (dict of Tensors): A dictionary containing gradient tensors to be vectorized.
        batch_dim (bool, optional): Whether to include the batch dimension in the
                                    returned tensor. Defaults to True.
        arr (Tensor, optional): An optional pre-allocated tensor to store the
                                vectorized gradients. If provided, it must have the
                                shape `[batch_size, num_params]`, where `num_params`
                                is the total number of scalar parameters in all
                                gradient tensors combined. If `None`, a new tensor
                                is created. Defaults to None.
        device (str, optional): "cuda" or "cpu". Defaults to "cuda".

    Returns:
        Tensor: If batch_dim is True, A 2D tensor of shape `[batch_size, num_params]`,
                where each row contains all the vectorized gradients for a single batch
                element. If batch_dim is False, A 1D tensor of shape `[num_params]`.

    Raises:
        ValueError: Parameter size in g doesn't match batch size.
    """
    if arr is None:
        if batch_dim:
            g_elt = g[next(iter(g.keys()))[0]]
            batch_size = g_elt.shape[0]
            num_params = 0
            for param in g.values():
                if param.shape[0] != batch_size:
                    msg = "Parameter row num doesn't match batch size."
                    raise ValueError(msg)
                num_params += int(param.numel() / batch_size)
            arr = torch.empty(size=(batch_size, num_params), dtype=g_elt.dtype,
                            device=device)
        else:
            num_params = 0
            for param in g.values():
                num_params += int(param.numel())
            arr = torch.empty(size=(num_params,), dtype=param.dtype,
                              device=device)

    pointer = 0
    vector_dim = 1
    for param in g.values():
        if batch_dim:
            if len(param.shape) <= vector_dim:
                num_param = 1
                p = param.data.reshape(-1, 1)
            else:
                num_param = param[0].numel()
                p = param.flatten(start_dim=1).data
            arr[:, pointer : pointer + num_param] = p.to(device)
            pointer += num_param
        else:
            num_param = param.numel()
            arr[pointer : pointer + num_param] = param.reshape(-1).to(device)
            pointer += num_param
    return arr


def flatten_params(tensors: Dict[str, torch.Tensor]) -> torch.Tensor:
    """Flatten a dictionary of tensors into a single tensor.

    This is useful for transforming model.named_parameters()
    results into a single tensor which can be passed to hvp or ihvp functions.

    Args:
        tensors: A dictionary of tensors. E.g., the result of model.named_parameters().

    Returns:
        (torch.Tensor): A single tensor containing the flattened parameters.

    Note:
        This function will flatten the tensors in the order they are passed in the
        dictionary and the flattened tensor will be a concatenation of all the tensors
        on one dimension.
    """
    return _vectorize(tensors, batch_dim=False,
                      device=tensors[next(iter(tensors.keys()))].device)


def _unflatten_params(tensors: torch.Tensor, model: torch.nn.Module) -> torch.Tensor:
    """Unflatten a single tensor into a dictionary of tensors.

    This is a reverse operation of flatten_params. The transforming could enable the
    following usage of `functional_call` function.

    Args:
        tensors: A single tensor containing the flattened parameters.
        model: A torch.nn.Module object, providing the shape information and the names
            of the parameters.

    Returns:
        (dict[str, torch.Tensor]): A dictionary of tensors.
            E.g., the result of model.named_parameters().

    Note:
        The returned value will use the `tensor` as the value of the dictionary, rather
        than directly returning model.named_parameters().
    """
    model_params = {k: p for k, p in model.named_parameters() if p.requires_grad}
    shape_list = [p.shape for p in model_params.values()]

    def generator() -> torch.Tensor:
        current_index = 0
        for shape in shape_list:
            size = math.prod(shape)
            yield tensors[current_index:current_index + size].reshape(shape)
            current_index += size
    return dict(zip(model_params.keys(), generator()))


def flatten_func(model: torch.nn.Module, param_num: int = 0) -> Callable:
    """A decorator that flattens the parameters of a function at a specified index.

    This is useful for working with functions that taking the dictionary of parameters
    as input, but we want to pass the flattened parameters to the function.

    Args:
        model: A torch.nn.Module object, providing the shape information and the names
            of the parameters.
        param_num: The index of the parameter that should be flattened.

    Returns:
        (Callable): A decorator that flattens the parameters of a function at the
            specified index.
    """
    def flatten_params_wrapper(function: Callable) -> Callable:
        """A wrapper function that flattens the parameters of a function.

        Args:
            function: The function to be wrapped.

        returns:
            (Callable): A wrapped function that flattens the parameters at the
                specified index.
        """
        @functools.wraps(function)
        def _function_flattened(*args, **kwargs: Dict[str, Any]) -> torch.Tensor:
            new_args = list(args)
            new_args[param_num] = _unflatten_params(args[param_num], model)
            return function(*new_args, **kwargs)
        return _function_flattened
    return flatten_params_wrapper
