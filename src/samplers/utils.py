from typing import Optional, Any, Dict, Tuple

import torch
import numpy as np
from torch import Tensor
from ..utils import SDE, linear_cg
from ..physics import BaseRayTrafo
from ..third_party_models import OpenAiUNetModel

def Euler_Maruyama_VE_sde_predictor( 
    score: OpenAiUNetModel,
    sde: SDE,
    x: Tensor,
    time_step: Tensor,
    step_size: float,
    nloglik: Optional[callable] = None,
    datafitscale: Optional[float] = None,
    penalty: Optional[float] = None,
    aTweedy: bool = False
    ) -> Tuple[Tensor, Tensor]:
    '''
    Implements the predictor step using Euler-Maruyama for the VE-SDE model
    (i.e., see Eq.30) in 
            1. @article{song2020score,
                title={Score-based generative modeling through stochastic differential equations},
                author={Song, Yang and Sohl-Dickstein, Jascha and Kingma, 
                    Diederik P and Kumar, Abhishek and Ermon, Stefano and Poole, Ben},
                journal={arXiv preprint arXiv:2011.13456},
                year={2020}
            }
    available at https://arxiv.org/abs/2011.13456.
    If ``aTweedy`` is True, implements the predictor method called ``Diffusion Posterior Sampling'', 
    presented in 
            2. @article{chung2022diffusion,
                title={Diffusion posterior sampling for general noisy inverse problems},
                author={Chung, Hyungjin and Kim, Jeongsol and Mccann, Michael T and Klasky, Marc L and Ye, Jong Chul},
                journal={arXiv preprint arXiv:2209.14687},
                year={2022}
            }, 
    available at https://arxiv.org/pdf/2209.14687.pdf.
    '''
    if nloglik is not None: assert (datafitscale is not None) and (penalty is not None)
    x.requires_grad_()
    s = score(x, time_step).detach() if not aTweedy else score(x, time_step)
    if nloglik is not None:
        if aTweedy: xhat0 = x + s*sde.marginal_prob_std(time_step)[:, None, None, None].pow(2)
        loss = nloglik(x if not aTweedy else xhat0)
        nloglik_grad = torch.autograd.grad(outputs=loss, inputs=x)[0]
    
    diffusion = sde.diffusion_coeff(time_step)[:, None, None, None]
    eta = diffusion.pow(2)*step_size
    update = s*eta
    x_mean = x + update
    if nloglik is not None: datafit = nloglik_grad * eta
    # if ``penalty == 1/σ2'' and ``aTweedy'' is False : recovers Eq.4 in 1.
    if aTweedy and nloglik is not None: datafitscale = loss.pow(-1)
    if nloglik is not None: x_mean = x_mean - penalty*datafit*datafitscale # minus for negative log-lik. 
    noise = eta.pow(.5)*torch.randn_like(x)
    x = x_mean + noise

    return x.detach(), x_mean.detach()

def Langevin_VE_sde_corrector(
    score: OpenAiUNetModel,
    sde: SDE,
    x: Tensor,
    time_step: Tensor,
    nloglik: Optional[callable] = None,
    datafitscale: Optional[float] = None,
    penalty: Optional[float] = None,
    corrector_steps: int = 1,
    snr: float = 0.16,
    ) -> Tensor:

    ''' 
    Implements the corrector step using Langevin MCMC for VE-SDE models.     
    '''
    if nloglik is not None: assert (datafitscale is not None) and (penalty is not None)
    for _ in range(corrector_steps):
        x.requires_grad_()
        s = score(x, time_step).detach()
        if nloglik is not None: nloglik_grad = torch.autograd.grad(outputs=nloglik(x), inputs=x)[0]
        overall_grad = s - penalty*nloglik_grad*datafitscale if nloglik is not None else s
        overall_grad_norm = torch.norm(
                overall_grad.reshape(overall_grad.shape[0], -1), 
                dim=-1  ).mean()
        noise_norm = np.sqrt(np.prod(x.shape[1:]))
        langevin_step_size = 2 * (snr * noise_norm / overall_grad_norm)**2
        x = x + langevin_step_size * overall_grad + torch.sqrt(2 * langevin_step_size) * torch.randn_like(x)
    return x.detach()

def decomposed_diffusion_sampling_VE_sde_predictor( 
    score: OpenAiUNetModel,
    sde: SDE, 
    x: Tensor,
    rhs: Tensor,
    time_step: Tensor,
    conj_grad_closure: callable, 
    eta: float, 
    step_size: float,
    cg_kwargs: Dict,
    datafitscale: Optional[float] = None
    ) -> Tuple[Tensor, Tensor]:

    '''
    It implements ``Decomposed Diffusion Sampling'' for the VE-SDE model 
        presented in 
            1. @article{chung2023fast,
                title={Fast Diffusion Sampler for Inverse Problems by Geometric Decomposition},
                author={Chung, Hyungjin and Lee, Suhyeon and Ye, Jong Chul},
                journal={arXiv preprint arXiv:2303.05754},
                year={2023}
            },
    available at https://arxiv.org/pdf/2303.05754.pdf. See Algorithm 4 in Appendix. 
    '''
    '''
    Implements the Tweedy denosing step proposed in ``Diffusion Posterior Sampling''. 
    '''
    datafitscale = 1. # place-holder

    s = score(x, time_step).detach()
    std = sde.marginal_prob_std(time_step)[:, None, None, None]
    xhat0 = x + s*std.pow(2) # Tweedy denoising step

    rhs_flat = rhs.reshape(np.prod(xhat0.shape[2:]), xhat0.shape[0])
    xhat0_flat = xhat0.reshape(np.prod(xhat0.shape[2:]), xhat0.shape[0])
    xhat, _= linear_cg( # data consistency step
        matmul_closure=conj_grad_closure,
        rhs=rhs_flat*1e-5 + xhat0_flat,
        initial_guess=xhat0.reshape(np.prod(xhat0.shape[2:]), xhat0.shape[0]),
        **cg_kwargs # early-stop CG (i.e., )
        )
    xhat = xhat.T.view(xhat0.shape[0], 1, *xhat0.shape[2:])
    '''
    It implemets the predictor sampling strategy presented in
        2. @article{song2020denoising,
            title={Denoising diffusion implicit models},
            author={Song, Jiaming and Meng, Chenlin and Ermon, Stefano},
            journal={arXiv preprint arXiv:2010.02502},
            year={2020}
        }
    available at https://arxiv.org/pdf/2010.02502.pdf.
    '''
    std_t = sde.marginal_prob_std(time_step)[:, None, None, None]
    std_tminus1 = sde.marginal_prob_std(time_step - step_size)[:, None, None, None]
    beta = 1 - std_tminus1.pow(2)/std_t.pow(2)
    noise_deterministic = - std_tminus1*std_t*torch.sqrt(1-beta.pow(2)*eta**2)*s
    noise_stochastic = std_tminus1*eta*beta*torch.randn_like(x)
    x = xhat + noise_deterministic + noise_stochastic

    return x.detach(), xhat

def conj_grad_closure(x: Tensor, ray_trafo: BaseRayTrafo, eps: float = 1e-5):
    batch_size = x.shape[-1]
    x = x.T.reshape(batch_size, 1, *ray_trafo.im_shape)
    return (eps*ray_trafo.trafo_adjoint(ray_trafo(x)) + x).view(batch_size, np.prod(ray_trafo.im_shape)).T

def chain_simple_init(
    time_steps: Tensor,
    sde: SDE, 
    filtbackproj: Tensor, 
    start_time_step: int, 
    im_shape: Tuple[int, int], 
    batch_size: int, 
    device: Any
    ):

    t = torch.ones(batch_size, device=device) * time_steps[start_time_step]
    std = sde.marginal_prob_std(t)[:, None, None, None]
    return filtbackproj + torch.randn(batch_size, *im_shape, device=device) * std