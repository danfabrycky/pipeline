#!/usr/bin/env python
""" 
    Pipestep HDR (High Dynamic Range)

    This module defines the pipeline step that corrects a pair raw image low
    and high gain images files for detector dark, bias, and flat effects.
    
    The step requires as input two files, a low gain file and a high gain file.
    It produces one output file.
    
    It uses StepLoadAux functions to call the following files:
        - masterpfit (x2): A 3D image containing two planes, constructed by performing
            a polynomial fit (currently linear) to a set of short-exposure darks.
            The first image has pixel values which are the slopes of linear fits
            to data from each pixel; the second image contains the intercepts and
            takes the place of a zero-exposure for the CMOS camera. Here, the
            intercept image is used for bias subtraction. Low and high gain.
        - masterdark (x2): A matched pair of high- and low-gain master darks of 
            the same exposure length as the raw sky image. MDARK or MMDARK files.
        - masterflat: A file consisting of three HDUs. The first contains a 3D
            flat-field image with a plane each for the high and low gain data
            (used for flat correction); the second contains a gain ratio image
            (used for creating an HDR output); the third contains a table of
            statistical information about the flats (not used here).
    
    Authors: Carmen Choza, Al Harper, Marc Berthoud
"""

from darepype.drp import DataFits # pipeline data object class
from darepype.drp.stepmiparent import StepMIParent # pipestep Multi-Input parent
from darepype.tools.steploadaux import StepLoadAux # pipestep steploadaux object class
from astropy.convolution import Gaussian2DKernel, interpolate_replace_nans # For masking/replacing
import scipy.ndimage as nd
import numpy as np
import logging

class StepHdr(StepLoadAux, StepMIParent):
    """ Pipeline Step Object to calibrate Flatfield High Dynamic Range files
    """
    
    stepver = '0.1' # pipe step version
    
    def __init__(self):
        """ Constructor: Initialize data objects and variables
        """
        # Call superclass constructor (calls setup)
        super(StepHdr,self).__init__()

        # Pfit values
        self.hpfitloaded = False # indicates if bias has been loaded
        self.hpfit = None # Numpy array object containing bias values
        self.hpfitname = '' # name of selected bias file
        
        self.lpfitloaded = False # indicates if bias has been loaded
        self.lpfit = None # Numpy array object containing bias values
        self.lpfitname = '' # name of selected bias file
        
        # Dark values
        self.hdarkloaded = False # indicates if high-gain dark has been loaded
        self.hdark = None # Numpy array object containing high-gain dark values
        self.hdarkname = '' # name of selected high-gain dark file
        
        self.ldarkloaded = False # indicates if low-gain dark has been loaded
        self.ldark = None # Numpy array object containing low-gain dark values
        self.ldarkname = '' # name of selected low-gain dark file
        
        # Flat values
        self.flatloaded = False # indicates if flat has been loaded
        self.flat = None # Numpy array object containing flat values
        self.flatname = '' # name of selected flat file
        
        # Finish up.
        self.log.debug('Init: done')
 
    def setup(self):
        """ ### Internal Names and Parameters need to be set Here ###
            
            Sets the internal names for the function and for saved files.
            - name: The name for the parameter. This name is used when
                    calling the pipe step from the command line or python shell.
                    It is also used to identify the step in the configuration file.
                    The name should be lower-case.
            - procname: Used to identify the step in saved output files.
                    The procname should be upper case.
                        
            Defines the input parameters for the current pipe step.
            The parameters are stored in a list containing the following
            information:
            - parameter name
            - default: A default value for the parameter. If nothing, set
                       to '', for strings, to 0 for integers and to 0.0 for floats.
            - help: A short description of the parameter.
        """
        ### SET NAMES

        # Set internal name of the pipeline reduction step.
        self.name='hdr'
        # Set procname.
        self.procname = 'HDR'
        
        ## SET UP PARAMETER LIST AND DEFAULT VALUES
        
        # Clear Parameter list.
        self.paramlist = []
        # Append parameters.
        self.paramlist.append(['reload', False,
            'Set to True to look for new pfit/flat/dark files for every input'])
        self.paramlist.append(['intermediate', False,
            'Set to T to include the result of the step'])
        self.paramlist.append(['splice_thresh', 3000.0,
            'Change to alter the cutoff threshold for combining high- and low-gain images'])
        # Set root names for loading parameters with StepLoadAux.
        self.loadauxsetup('lpfit')
        self.loadauxsetup('hpfit')
        self.loadauxsetup('ldark') 
        self.loadauxsetup('hdark')
        self.loadauxsetup('flat')
        
        
        ## SET LOGGER AND FINISH UP
        
        # Set Logger for this pipe step.
        self.log = logging.getLogger('stoneedge.pipe.step.%s' % self.name)  
        # Confirm end of setup.
        self.log.debug('Setup: done')
        
    def run(self):
        """ Runs the correction algorithm. The corrected data is
            returned in self.dataout
        """
        ### Load pfit, dark and flat files
        # Set loaded flags to false if reload flag is set
        if self.getarg('reload'):
            self.lpfitloaded = False
            self.hpfitloaded = False
            self.hdarkloaded = False
            self.ldarkloaded = False
            self.flatloaded = False
        # Load pfit file
        if not self.hpfitloaded:
            self.hpfitname = self.loadauxfile('hpfit', multi = False)
            self.hpfit = DataFits(config = self.config)
            self.hpfit.load(self.hpfitname)
            self.hpfitloaded = True
        if not self.lpfitloaded:
            self.lpfitname = self.loadauxfile('lpfit', multi = False)
            self.lpfit = DataFits(config = self.config)
            self.lpfit.load(self.lpfitname)
            self.lpfitloaded = True
        # Load mdark file
        if not self.hdarkloaded:
            self.hdarkname = self.loadauxfile('hdark', multi = False)
            self.hdark = DataFits(config = self.config)
            self.hdark.load(self.hdarkname)
            self.hdarkloaded = True
        if not self.ldarkloaded:
            self.ldarkname = self.loadauxfile('ldark', multi = False)
            self.ldark = DataFits(config = self.config)
            self.ldark.load(self.ldarkname)
            self.ldarkloaded = True
        # Load mflat file
        if not self.flatloaded:
            self.flatname = self.loadauxfile('flat', multi = False)
            self.flat = DataFits(config = self.config)
            self.flat.load(self.flatname)
            self.flatloaded = True
        
        # Get all relevant image data
        hbias = self.hpfit.image[1]   # high-gain bias from polyfit
        lbias = self.lpfit.image[1]   # low-gain bias from polyfit
        
        hdark = self.hdark.image      # high-gain dark
        ldark = self.ldark.image      # low-gain dark
        
        gain = self.flat.imageget('gain ratio')    # high-gain flat divided by low-gain flat, ratio between modes
        hflat = self.flat.image[1]                 # high-gain flat
        lflat = self.flat.image[0]                 # low-gain flat
        
        
        ### The images are now in DataFits objects
        
        # Get the filename to determine gain
        filename1 = self.datain[0].filenamebegin
        filename2 = self.datain[1].filenamebegin
        
        if 'bin1L' in filename1:
            dataH_df = self.datain[1]
            if 'RAW.fits' in filename1:
                dL = self.datain[0]
                self.ldata_df = DataFits(config = self.config)
                ldata_df.header = dL.getheader(dL.imgnames[1]).copy()
                del dL.header['xtension']
                ldata_df.header.insert(0,('simple',True,'file does conform to FITS standard'))
                ldata_df.imageset(dL.imageget(dL.imgnames[1]))
            else:
                dataL_df = self.datain[0]
        elif 'bin1H' in filename1:
            self.dataH_df = self.datain[0]
            if 'RAW.fits' in filename1:
                dL = self.datain[1]
                self.ldata_df = DataFits(config = self.config)
                ldata_df.header = dL.getheader(dL.imgnames[1]).copy()
                del dL.header['xtension']
                ldata_df.header.insert(0,('simple',True,'file does conform to FITS standard'))
                ldata_df.imageset(dL.imageget(dL.imgnames[1]))
            else:
                ldata_df = self.datain[1]
       
        # dataL_df now contains the low-gain file, dataH_df now contains the high-gain file:
        
        hdata = hdata_df.image
        ldata = ldata_df.image
        
        '''Process high-gain data'''
        
        hdatabdf = ((hdata - hbias) - (hdark - hbias))/hflat
        
        # Create a hot pixel mask from the input dark.
        
        
        
        '''Process low-gain data'''
        
        ldatabdf = (((ldata-lbias) - (ldark - lbias))/lflat) * gain
        
        '''Combine high- and low-gain data into HDR image'''
        splice_thresh = self.getarg('splice_thresh') # Get crossover threshold
        lupper = np.where(ldatabdf > splice_thresh) # Choose all pixels in low-gain data above a certain threshold parameter
        ldata = ldatabdf.copy()
        HDRdata = hdatabdf.copy()
        HDRdata[lupper] = ldata[lupper]      # Replace upper range of high-gain image with low-gain * gain values
        
        '''Downsample image by factor of two'''
        outdata = nd.zoom(HDRdata,0.5)
        
        # Make dataout
        self.dataout = self.datain[0].copy() # could also be new DataFits() or copy of datain[1]]
        
        self.dataout.image = outdata
        self.dataout.header = hdata_df.header.copy()
        
        ## Construct an output name.

        a = filename1.split('_')
        b = '_'
        newname = a[0]+b+a[1]+b+a[2]+b+a[3][:-1]+b+a[4]+b+a[5]+b+a[6]+b+a[7]+b+'HDR.fits'
        
        self.dataout.filename = newname
        
    def reset(self):
        """ Resets the step to the same condition as it was when it was
            created. Internal variables are reset, any stored data is
            erased.
        """
        self.pfitloaded = False
        self.pfit = None
        self.darkloaded = False
        self.dark = None
        self.flatloaded = False
        self.flat = None
        self.log.debug('Reset: done')

if __name__ == '__main__':
    """ Main function to run the pipe step from command line on a file.
        Command:
        python stepparent.py input.fits -arg1 -arg2 . . .
        Standard arguments:
        --config=ConfigFilePathName.txt : name of the configuration file
        --test : runs the functionality test i.e. pipestep.test()
        --loglevel=LEVEL : configures the logging output for a particular level
    """
    StepHdr().execute()

'''HISTORY:
2022-1-5 - Set up file, most code copied from StepBiasDarkFlat - Marc Berthoud
'''