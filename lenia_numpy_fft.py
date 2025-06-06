import numpy as np
import pickle
import os
import random
import pygame
import imageio.v2 as imageio
import matplotlib.pyplot as plt
from utils.voronoi_polygons import load_pattern
from scipy.fft import fft2, ifft2

class LeniaParams:
    """NumPy version of LeniaParams to store and manage Lenia parameters."""
    def __init__(self, batch_size=1, k_size=25, channels=3, device=None, param_dict=None):
        self.batch_size = batch_size
        self.k_size = k_size
        self.channels = channels
        
        if param_dict is not None:
            self.param_dict = param_dict
            self.mu = np.array(param_dict['mu'])
            self.sigma = np.array(param_dict['sigma'])
            self.beta = np.array(param_dict['beta'])
            self.mu_k = np.array(param_dict['mu_k'])
            self.sigma_k = np.array(param_dict['sigma_k'])
            self.weights = np.array(param_dict['weights'])
            self.k_size = param_dict.get('k_size', k_size)
            self.batch_size = self.weights.shape[0]
        else:
            # Initialize with default values
            self.mu = np.array([[[0.1]]])
            self.sigma = np.array([[[0.015]]])
            self.beta = np.array([[[[1.0]]]])
            self.mu_k = np.array([[[[0.5]]]])
            self.sigma_k = np.array([[[[0.15]]]])
            self.weights = np.array([[[1.0]]])
            self.param_dict = {
                'k_size': self.k_size,
                'mu': self.mu,
                'sigma': self.sigma,
                'beta': self.beta,
                'mu_k': self.mu_k,
                'sigma_k': self.sigma_k,
                'weights': self.weights
            }
    
    def to(self, device):
        # This is a no-op for NumPy, kept for API compatibility
        pass
    
    def __getitem__(self, key):
        return self.param_dict[key]


class Automaton:
    """Base automaton class."""
    def __init__(self, size):
        self.h, self.w = size
        self._worldmap = None
        self.worldsurface = None
    
    @property
    def worldmap(self):
        return self._worldmap
    
    @worldmap.setter
    def worldmap(self, value):
        self._worldmap = value
        
    def draw(self):
        if self._worldmap is not None:
            # Convert from NumPy array
            array = np.asarray(self._worldmap.transpose(1, 2, 0) * 255).astype(np.uint8)
            
            # Create pygame surface from the array
            self.worldsurface = pygame.surfarray.make_surface(array)
            
            return self._worldmap
        return None


class MultiLeniaNumPy(Automaton):
    """
    Multi-channel Lenia automaton implemented in NumPy.
    A multi-colored GoL-inspired continuous automaton. Originally introduced by Bert Chan.
    """
    def __init__(self, size, batch=1, dt=0.1, num_channels=3, params=None, param_path=None, device='cpu'):
        """
        Initializes automaton.  

        Args:
            size: (H,W) of ints, size of the automaton
            batch: int, batch size for parallel simulations
            dt: float, time-step used when computing the evolution
            num_channels: int, number of channels (C)
            params: LeniaParams class or dict of parameters
            param_path: str, path to folder containing saved parameters
            device: str, device (not used in NumPy implementation)
        """
        super().__init__(size=size)

        self.batch = batch
        self.C = num_channels
        self.device = device  # Not used in NumPy, kept for compatibility

        if params is None:
            self.params = LeniaParams(batch_size=self.batch, k_size=25, channels=self.C)
        elif isinstance(params, dict):
            self.params = LeniaParams(param_dict=params)
        else:
            self.params = params

        kernel_folder = "_".join([str(s) for s in self.params["mu_k"][0,0,0]])+"_"+"_".join([str(s) for s in self.params["sigma_k"][0,0,0]])
        self.kernel_path = "unif_random_voronoi/" + kernel_folder
        print(self.kernel_path)

        self.g_mu = np.round(params["mu"].item(), 4)
        self.g_sig = np.round(params["sigma"].item(), 4)

        self.data_path = f"unif_random_voronoi/{kernel_folder}/data/{self.g_mu}_{self.g_sig}.pickle"

        self.k_size = self.params['k_size']

        # Create a random initial state
        self.state = np.random.uniform(size=(self.batch, self.C, self.h, self.w))

        # Load polygons for initialization
        try:
            with open(f'utils/polygons{self.h}.pickle', 'rb') as handle:
                self.polygons = pickle.load(handle)
        except:
            print("polygons for this array size not generated yet")
            self.polygons = None

        self.dt = dt

        # Compute normalized weights
        self.normal_weights = self.norm_weights(self.params.weights)
        
        # Compute kernel and its FFT
        self.kernel = self.compute_kernel()
        self.fft_kernel = self.kernel_to_fft(self.kernel)

        ii, jj = np.meshgrid(np.arange(0, self.w), np.arange(0, self.h), indexing='ij')
        # Stack and reshape coordinates
        coords = np.stack([np.reshape(ii, (-1,)), np.reshape(jj, (-1,))], axis=-1)
        coords = coords.astype(np.float32)

        # Reshape to (array_size^2, 2, 1)
        self.coords = np.reshape(coords, (self.w*self.h, 2, 1))

        # For loading and saving parameters
        self.saved_param_path = param_path
        if self.saved_param_path is not None:
            self.param_files = [file for file in os.listdir(self.saved_param_path) if file.endswith('.pt')]
            self.num_par = len(self.param_files)
            if self.num_par > 0:
                self.cur_par = random.randint(0, self.num_par-1)
            else:
                self.cur_par = None

        self.to_save_param_path = 'SavedParameters/Lenia'

    def to(self, device):
        # This is a no-op for NumPy, kept for API compatibility
        pass
    
    def update_params(self, params, k_size_override=None):
        """
        Updates parameters of the automaton.
        
        Args:
            params: LeniaParams object
            k_size_override: int, override the kernel size of params
        """
        if k_size_override is not None:
            self.k_size = k_size_override
            if self.k_size % 2 == 0:
                self.k_size += 1
                print(f'Increased even kernel size to {self.k_size} to be odd')
            params.k_size = self.k_size
        
        self.params = LeniaParams(param_dict=params.param_dict)
        self.batch = self.params.batch_size
        
        # Update derived parameters
        self.normal_weights = self.norm_weights(self.params.weights)
        self.kernel = self.compute_kernel()
        self.fft_kernel = self.kernel_to_fft(self.kernel)

    @staticmethod
    def norm_weights(weights):
        """
        Normalizes the relative weight sum of the growth functions.
        
        Args:
            weights: (B,C,C) array of weights
            
        Returns:
            (B,C,C) array of normalized weights
        """
        # Sum weights along the first dimension
        sum_weights = np.sum(weights, axis=1, keepdims=True)  # (B,1,C)
        
        # Normalize weights, avoiding division by zero
        return np.where(sum_weights > 1e-6, weights / sum_weights, 0)

    def set_init_voronoi_batch(self, polygon_size=60, init_polygon_index=0, seeds=None):
        """
        Initialize state using Voronoi polygons.
        
        Args:
            polygon_size: int, size of polygons
            init_polygon_index: int, starting index for polygons
            seeds: list of ints, random seeds for initialization
        """
        if not seeds:
            seeds = [np.random.randint(2**32) for _ in range(self.batch)]
        elif not len(seeds) == self.batch:
            print("number of seeds does not match batch size, reinitializing seeds")
            seeds = [np.random.randint(2**32) for _ in range(self.batch)]

        self.seeds = seeds
        print(seeds)

        # Create empty numpy array for states
        states_np = np.empty((self.batch, self.C, self.h, self.w))
        
        for i, seed in enumerate(seeds):
            polygon_index = init_polygon_index + i
            mask = self.polygons[polygon_size][polygon_index % 1024]
            mask = load_pattern(mask.reshape(1, *mask.shape), [self.h, self.w]).reshape(self.h, self.w)

            np.random.seed(seed)
            
            # Generate random state and apply mask
            rand_np = np.random.rand(1, self.C, self.h, self.w)
            pattern = np.asarray(rand_np * mask)
            states_np[i] = pattern[0]
        
        # Update state
        self.state = states_np

    def plot_voronoi_batch(self, figsize=(15, 10), save_path="inits.png"):
        """
        Creates and saves a matplotlib figure with all states from a Lenia object.
        
        Args:
            figsize: Tuple (width, height) for the figure size
            save_path: String path where to save the plot
        """
        cmap = "gray"
        # Get the states from the Lenia object
        states = self.state  # Shape: (batch, channels, height, width)
        batch_size, channels, height, width = states.shape
        
        # Calculate the grid dimensions for the subplots
        grid_cols = int(np.ceil(np.sqrt(batch_size)))
        grid_rows = int(np.ceil(batch_size / grid_cols))
        
        # Create the figure and subplots
        fig, axes = plt.subplots(grid_rows, grid_cols, figsize=figsize)
        
        # Flatten the axes array for easier indexing
        if batch_size > 1:
            axes = axes.flatten()
        else:
            axes = [axes]  # Handle the case of a single plot
        
        # Plot each state
        for i in range(batch_size):
            # For multichannel data, combine channels
            if channels > 1:
                # Create RGB image if 3 channels, otherwise use first channel
                if channels == 3:
                    state_img = np.transpose(states[i], (1, 2, 0))
                    im = axes[i].imshow(state_img)
                else:
                    im = axes[i].imshow(states[i][0], cmap=cmap)
            else:
                im = axes[i].imshow(states[i][0], cmap=cmap)
            
            axes[i].set_title(f"State {i}")
            axes[i].axis('off')  # Remove axes for cleaner look
        
        # Hide any unused subplots
        for i in range(batch_size, len(axes)):
            axes[i].axis('off')
        
        # Adjust spacing
        plt.tight_layout()
        
        # Save the figure
        plt.savefig(save_path, dpi=300, bbox_inches='tight')
        
        print(f"Plot saved to {save_path}")
        
        return fig, axes

    def kernel_slice(self, r):
        """
        Given a distance matrix r, computes the kernel of the automaton.
        
        Args:
            r: (k_size,k_size) array, value of the radius for each pixel of the kernel
            
        Returns:
            (B,C,C,k_size,k_size) array of kernel values
        """
        # Expand radius to match expected kernel shape
        r = r[None, None, None, None, :, :]  # (1,1,1,1,k_size,k_size)
        
        # Get number of cores
        num_cores = self.params.mu_k.shape[3]
        
        # Expand r to match batched parameters
        r = np.broadcast_to(r, (self.batch, self.C, self.C, num_cores, self.k_size, self.k_size))
        
        # Reshape parameters for broadcasting
        mu_k = self.params.mu_k[:, :, :, :, None, None]  # (B,C,C,#cores,1,1)
        sigma_k = self.params.sigma_k[:, :, :, :, None, None]  # (B,C,C,#cores,1,1)
        beta = self.params.beta[:, :, :, :, None, None]  # (B,C,C,#cores,1,1)
        
        # Compute kernel
        K = np.exp(-((r - mu_k) / sigma_k)**2 / 2)  # (B,C,C,#cores,k_size,k_size)
        
        # Sum over cores with respective heights
        K = np.sum(beta * K, axis=3)  # (B,C,C,k_size,k_size)
        
        return K

    def compute_kernel(self):
        """
        Computes the kernel given the current parameters.
        
        Returns:
            (B,C,C,k_size,k_size) array of kernel values
        """
        # Create coordinate grid
        xyrange = np.linspace(-1, 1, self.k_size)
        x, y = np.meshgrid(xyrange, xyrange, indexing='xy')
        
        # Compute radius values
        r = np.sqrt(x**2 + y**2)
        
        # Compute kernel
        K = self.kernel_slice(r)  # (B,C,C,k_size,k_size)
        
        # Normalize kernel
        summed = np.sum(K, axis=(-1, -2), keepdims=True)  # (B,C,C,1,1)
        summed = np.where(summed < 1e-6, 1.0, summed)  # Avoid division by zero
        K = K / summed
        
        return K

    def kernel_to_fft(self, K):
        """
        Computes the Fourier transform of the kernel.
        
        Args:
            K: (B,C,C,k_size,k_size) array, the kernel
            
        Returns:
            (B,C,C,h,w) array, the FFT of the kernel
        """
        # Create padded kernel
        padded_K = np.zeros((self.batch, self.C, self.C, self.h, self.w))
        
        # Place kernel in the center
        k_h, k_w = self.k_size, self.k_size
        start_h, start_w = self.h // 2 - k_h // 2, self.w // 2 - k_w // 2
        
        # Update padded kernel with actual kernel values
        padded_K[:, :, :, start_h:start_h+k_h, start_w:start_w+k_w] = K
        
        # Shift for FFT
        padded_K = np.roll(padded_K, [-self.h // 2, -self.w // 2], axis=(-2, -1))
        
        # Compute FFT
        return fft2(padded_K)

    def growth(self, u):
        """
        Computes the growth function applied to concentrations u.
        
        Args:
            u: (B,C,C,h,w) array of concentrations
            
        Returns:
            (B,C,C,h,w) array of growth values
        """
        # Reshape parameters for broadcasting
        mu = self.params.mu[:, :, :, None, None]  # (B,C,C,1,1)
        sigma = self.params.sigma[:, :, :, None, None]  # (B,C,C,1,1)
        
        # Broadcast to match u's shape
        mu = np.broadcast_to(mu, u.shape)
        sigma = np.broadcast_to(sigma, u.shape)
        
        # Compute growth function (Gaussian bump)
        return 2 * np.exp(-((u - mu)**2 / (sigma)**2) / 2) - 1

    def get_fftconv(self, state):
        """
        Compute convolution of state with kernel using FFT.
        
        Args:
            state: (B,C,h,w) array, the current state
            
        Returns:
            (B,C,C,h,w) array of convolution results
        """
        # Compute FFT of state
        fft_state = fft2(state)  # (B,C,h,w)
        
        # Reshape for broadcasting with kernel
        fft_state = fft_state[:, :, None, :, :]  # (B,C,1,h,w)
        
        # Multiply in frequency domain
        convolved = fft_state * self.fft_kernel  # (B,C,C,h,w)
        
        # Inverse FFT
        result = ifft2(convolved)  # (B,C,C,h,w)
        
        return np.real(result)

    def step(self):
        """
        Steps the automaton state by one iteration.
        """
        # Compute convolutions
        convs = self.get_fftconv(self.state)  # (B,C,C,h,w)
        
        # Compute growth
        growths = self.growth(convs)  # (B,C,C,h,w)
        
        # Apply weights
        weights = self.normal_weights[:, :, :, None, None]  # (B,C,C,1,1)
        weights = np.broadcast_to(weights, growths.shape)  # (B,C,C,h,w)
        
        # Sum weighted growths
        dx = np.sum(growths * weights, axis=1)  # (B,C,h,w)
        
        # Update state
        self.state = np.clip(self.state + self.dt * dx, 0, 1)  # (B,C,h,w)

    def mass(self):
        """
        Computes average 'mass' of the automaton for each channel.
        
        Returns:
            (B,C) array, mass of each channel
        """
        return np.mean(self.state, axis=(-1, -2))  # (B,C)
    
    def get_batch_mass_center(self, array):
        """
        Calculate the center of mass for each batch and channel.
        
        Args:
            array: (B,C,H,W) array, the current state
            
        Returns:
            tuple of (2, B*C) array of center coordinates and (B*C) array of masses
        """
        B, C, H, W = array.shape  # array shape: (B,C,H,W)
        
        # Reshape array to (H*W, 1, B*C)
        A = np.transpose(array, (2, 3, 0, 1)).reshape(H*W, 1, B*C)
        
        # Calculate total mass
        total_mass = np.sum(A, axis=0)[-1]  # (B*C)
        
        # Calculate weighted sum by coordinates
        prod = A * self.coords
        sum_mass = np.sum(prod, axis=0)  # (2, B*C)
        
        # Create a mask for non-zero masses
        mask = (total_mass != 0)
        
        # Normalize by total mass where total mass is not zero
        sum_mass[:, mask] = sum_mass[:, mask] / total_mass[mask]
    
        return sum_mass, total_mass

    def draw(self):
        """
        Draws the RGB worldmap from state.
        """
        assert self.state.shape[0] == 1, "Batch size must be 1 to draw"
        toshow = self.state[0]  # (C,h,w)

        if self.C == 1:
            # Expand to 3 channels
            toshow = np.broadcast_to(toshow, (3, self.h, self.w))
        elif self.C == 2:
            # Add zero channel
            zeros = np.zeros((1, self.h, self.w))
            toshow = np.concatenate([toshow, zeros], axis=0)
        else:
            # Use only first 3 channels
            toshow = toshow[:3, :, :]
    
        self._worldmap = toshow
        
        # Create pygame surface
        super().draw()
        
        return toshow

    def make_video(self, seeds=None, polygon_size=60, init_polygon_index=0, sim_time=200, step_size=2, phase=None, save_path=None):
        """
        Create a video from the simulation frames.
        
        Args:
            seeds: list of ints, random seeds for initialization
            polygon_size: int, size of polygons
            init_polygon_index: int, starting index for polygons
            sim_time: int, number of simulation steps
            step_size: int, interval between saved frames
            phase: str, optional phase identifier for filename
            save_path: str, path to save the video
        """
        if seeds is None:
            seeds = [np.random.randint(2**32) for _ in range(self.batch)]
        
        self.set_init_voronoi_batch(polygon_size=polygon_size, init_polygon_index=init_polygon_index, seeds=seeds)
        
        # Create directory for frames
        frames_dir = "frames"
        os.makedirs(frames_dir, exist_ok=True)

        pygame.init()
        pygame.display.set_mode((500, 500))

        frame_count = 0
        
        for t in range(sim_time):
            self.step()
    
            if t % step_size == 0:  # Save every nth frame to reduce file count
                self.draw()
                pygame.image.save(self.worldsurface, f"{frames_dir}/frame_{frame_count:04d}.png")
                frame_count += 1
        
                # Update display
                pygame.display.get_surface().blit(self.worldsurface, (0, 0))
                pygame.display.flip()

        # Save final state
        self.draw()

        if not save_path:
            video_dir = f"{self.kernel_path}/videos"
            os.makedirs(video_dir, exist_ok=True)
            if not phase:
                save_path = f"{video_dir}/{polygon_size}_{init_polygon_index}_{seeds[0]}_numpy.gif"
            else:
                save_path = f"{video_dir}/{polygon_size}_{phase}_{init_polygon_index}_{seeds[0]}_numpy.gif"

        # Create video from frames
        frames = [imageio.imread(f"{frames_dir}/frame_{i:04d}.png") for i in range(frame_count)]
        imageio.mimsave(save_path, frames, fps=30)

        pygame.quit()


#---------------------------------EXAMPLE---------------------------------

# Set up parameters
params = {
    'k_size': 27, 
    'mu': np.array([[[0.15]]]), 
    'sigma': np.array([[[0.015]]]), 
    'beta': np.array([[[[1.0]]]]), 
    'mu_k': np.array([[[[0.5]]]]), 
    'sigma_k': np.array([[[[0.15]]]]), 
    'weights': np.array([[[1.0]]])
}

# Create Lenia instance
polygon_size = 25
lenia = MultiLeniaNumPy((100, 100), batch=32, num_channels=1, dt=0.1, params=params)

#lenia.make_video(polygon_size=polygon_size, sim_time=200, step_size=2)