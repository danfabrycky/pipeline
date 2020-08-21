#!/usr/bin/env python
"""
    Pipestep SrcExtPy

    This module defines the pipeline step to extract sources from data files.
    The pipe step runs the python library SEP on the data in order to extract
    the sources and calculate a first order approximation of flux and magnitude

    For more info check out the read the docs for SEP: https://sep.readthedocs.io/

   This uses the SEP python library for source extraction.
    Author: Amanda Pagul / Marc Berthoud/ Daniel Sharkey

    Update 8/6/20 by Daniel Sharkey
    Here I have attempted to convert the original Fluxcalsex step into a 
    Sextract step that uses the python library SEP



"""
import os # os library
import sys # sys library
import numpy as np # numpy library
import scipy # scipy library
import string # string library
import logging # logging object library
import subprocess # running a subprocess library
import requests # http request library
import astropy.table # Read astropy tables
import sep # Extracts Sources and Calculates Flux
from astropy.io import fits
from astropy.io import ascii
from astropy.coordinates import SkyCoord # To make RA/Dec as float
from astropy import units as u # To help with SkyCoord
import matplotlib # to make plots
matplotlib.use('Agg') # Set pixel image
import pylab as plt # pylab library for plotting
from lmfit import minimize, Parameters # For brightness correction fit
from darepype.drp import StepParent # pipestep stepparent object


class StepSrcExtPy(StepParent):
    """ Pipeline Step Object to calibrate Bias/Dark/Flat files
    """

    stepver = '0.1' # pipe step version


    def setup(self):
        """ ### Names and Parameters need to be Set Here ###
            Sets the internal names for the function and for saved files.
            Defines the input parameters for the current pipe step.
            The parameters are stored in a list containing the following
            information:
            - name: The name for the parameter. This name is used when
                    calling the pipe step from command line or python shell.
                    It is also used to identify the parameter in the pipeline
                    configuration file.
            - default: A default value for the parameter. If nothing, set
                       '' for strings, 0 for integers and 0.0 for floats
            - help: A short description of the parameter.
        """
        ### Set Names
        # Name of the pipeline reduction step
        self.name='SrcExtPy'
        # Shortcut for pipeline reduction step and identifier for
        # saved file names.
        self.procname = 'sepext'
        # Set Logger for this pipe step
        self.log = logging.getLogger('pipe.step.%s' % self.name)
        ### Set Parameter list
        # Clear Parameter list
        self.paramlist = []
        # Append parameters
        self.paramlist.append(['sourcetable',False,
                               'Flag for making txt table of all sources'])
        self.paramlist.append(['sourcetableformat','csv',
                               'txt table format (see astropy.io.ascii for options)'])
        # confirm end of setup
        self.log.debug('Setup: done')

    def run(self):
        """ Runs the calibrating algorithm. The calibrated data is
            returned in self.dataout
        """
        ### Preparation
        binning = self.datain.getheadval('XBIN')
        ### Run Source Extractor
        # Make sure input data exists as file
        if not os.path.exists(self.datain.filename) :
            self.datain.save()
        '''
        # Make catalog filename
        catfilename = self.datain.filenamebegin
        if catfilename[-1] in '._-': catfilename += 'sex_cat.fits'
        else: catfilename += '.sex_cat.fits'
        # Make background filename (may not be used - see below)
        bkgdfilename = self.datain.filenamebegin
        if bkgdfilename[-1] in '._-': bkgdfilename += 'SxBkgd.fits'
        else: bkgdfilename += '_SxBkgd.fits'
        self.log.debug('Sextractor catalog filename = %s' % catfilename)
		'''
        #Open data out of fits file for use in SEP
        image = self.datain.image

        #Set values for variables used later
        #These variables are used for the background analysis. bw and bh I found just testing various numbers
        maskthresh = 0.0
        bw, bh = 10, 10
        fw, fh = 3, 3
        fthresh = 0.0



        #Create the background image and it's error
        bkg = sep.Background(image, maskthresh=maskthresh,bw=bw, bh=bh, fw=fw,
        fh=fh, fthresh=fthresh) #have sep determine the background of the image

        bkg_image=bkg.back()

        bkg_rms =bkg.rms()
        #Subtract the background from the image
        image_sub = image - bkg_image

        imsubmed = np.nanmedian(image_sub)
        imsubmad = mad_std(image_sub)



		#Create variables that are used during source Extraction
        extract_thresh = 5
        extract_err = bkg_rms

        #Extract sources from the subtracted image
        objects = sep.extract(image_sub, extract_thresh, err=extract_err)

        #Define variables used later during flux calculation
        sum_c = np.zeroes(len(objects))
        sum_c_err = np.zeroes(len(objects))
        sum_c_flags = np.zeroes(len(objects))
        ratio = np.zeroes(len(objects))
        rmax = np.zeroes(len(objects))
        dx = np.zeros(len(objects))
        dy = np.zeros(len(objects))



        #Do basic uncalibrated measurments of flux for use in step astrometry. 
      
        #First we calculate flux using Ellipses. In order to do this we need to calculate the Kron Radius
        #For the ellipses Extract identified using the ellipse parameters it gives
        #R is equal to 6 as that is the default used in Source Extractor
        kronrad, krflag = sep.kron_radius(image_sub, objects['x'], objects['y'], 
        	objects['a'], objects['b'], objects['theta'], r=6)

        #Using this Kron radius we calculate the flux, this is equivallent to FLUX_AUTO in SExtractor
        flux_elip, fluxerr_elip, flag = sep.sum_ellipse(image_sub, objects['x'], 
        	objects['y'], objects['a'], objects['b'], objects['theta'], 
        	2.5*kronrad, err=bkg_rms, subpix=1)

		#Then we calculate it using Circular Apetures, this will be used to remove sources that are too elipitical
        flux_circ, fluxerr_circ, flag = sep.sum_circle(image_sub,
			objects['x'], objects['y'], r=2.5, err = bkg_rms,subpix=1)




        ### Extract catalog from source extractor and clean up dataset
        # Use catalog from sourse extrator (test.cat)
        #seo_catalog = astropy.table.Table.read(catfilename, format="fits", hdu='LDAC_OBJECTS')
        seo_Mag = -2.5*np.log10(flux_elip)
        seo_MagErr = (2.5/np.log(10)*(fluxerr_elip/flux_elip))
        
        # Select only the stars in the image: circular image and S/N > 10
        elongation = (flux_circ-flux_elip)<250
        seo_SN = ((flux_elip/fluxerr_elip)>10)
        seo_SN = (seo_SN) & (elongation) & ((flux_elip/fluxerr_elip)<1000) & (fluxerr_elip != 0)
        self.log.debug('Selected %d stars from Source Extrator catalog' % np.count_nonzero(seo_SN))
 
        ### Query and extract data from Guide Star Catalog
        # Get RA / Dec
       
        '''
        ra_center =  self.datain.getheadval('RA' ).split(':')
        dec_center = self.datain.getheadval('DEC').split(':')
        ra_cent =  ' '.join([str(s) for s in ra_center])
        dec_cent = ' '.join([str(s) for s in dec_center])
        center_coordinates = SkyCoord(ra_cent + ' ' + dec_cent, unit=(u.hourangle, u.deg) )
        self.log.debug('Using RA/Dec = %s / %s' % (center_coordinates.ra, center_coordinates.dec) )
        # Querry guide star catalog2 with center coordinates
        gsc2_query = 'http://gsss.stsci.edu/webservices/vo/CatalogSearch.aspx?'
        gsc2_query += 'RA='+str(center_coordinates.ra.value)
        gsc2_query += '&DEC='+str(center_coordinates.dec.value)
        gsc2_query += '&DSN=+&FORMAT=CSV&CAT=GSC241&SR=0.5&'
        self.log.debug('Running URL = %s' % gsc2_query)
        gsc2_result = requests.get(gsc2_query)
        # Get data from result
        filter_map = self.getarg('filtermap').split('|')
        filter_name = filter_tel = self.datain.getheadval('FILTER')
        for fil in filter_map:
            entry = fil.split('=')
            if entry[0] == filter_tel:
                try:
                    filter_name = entry[1]
                except:
                    self.log.error("Badly formatted filter mapping. No '=' after %s"
                                   % filter_tel)
        query_table = astropy.io.ascii.read(gsc2_result.text)
        table_filter = 'SDSS'+filter_name+'Mag'
        table_filter_err = 'SDSS'+filter_name+'MagErr'
        GSC_RA = query_table['ra'][(query_table[table_filter]<22) & (query_table[table_filter]>0)]
        GSC_DEC = query_table['dec'][(query_table[table_filter]<22) & (query_table[table_filter]>0)]
        GSC_Mag = query_table[table_filter][(query_table[table_filter]<22) & (query_table[table_filter]>0)]
        GSC_MagErr = query_table[table_filter_err][(query_table[table_filter]<22) & (query_table[table_filter]>0)]
        self.log.debug('Received %d entries from Guide Star Catalog' % len(GSC_RA))
        ### Mach Guide Star Catalog data with data from Source Extractor
        # Do the matching
        seo_radec = SkyCoord(ra=seo_catalog['ALPHA_J2000'], dec=seo_catalog['DELTA_J2000'])
        GSC_radec = SkyCoord(ra=GSC_RA*u.deg, dec=GSC_DEC*u.deg)
        idx, d2d, d3d = GSC_radec.match_to_catalog_sky(seo_radec[seo_SN])
        # only select objects less than 0.025 away in distance, get distance value
        dist_value = 1*0.76*binning/3600. #Maximum distance is 1 pixel
        mask = d2d.value<dist_value
        if(np.sum(mask) < 2):
            self.log.warn('Only %d sources match between image and guide star catalog, fit may not work' %
                          np.sum(mask) )
        self.log.debug('Distance_Value = %f, Min(distances) = %f, Mask length = %d' %
                       ( dist_value, np.min(d2d.value), np.sum(mask) ) )
        ### Calculate the fit correction between the guide star and the extracted values
        # Make lambda function to be minimized
        # The fit finds m_ml and b_ml where
        #     seo_Mag = b_ml + m_ml * GSC_Mag
        nll = lambda *args: -residual(*args)
        # Get errors
        eps_data = np.sqrt(GSC_MagErr**2+seo_MagErr[seo_SN][idx]**2)
        # Make estimate for intercept to give as initial guess
        b_ml0 = np.median(seo_Mag[seo_SN][idx][mask]-GSC_Mag[mask])
        self.log.debug('Offset guess is %f mag' % b_ml0)
        # Calculate distance from that guess and get StdDev of distances
        guessdistances = np.abs( b_ml0 - ( seo_Mag[seo_SN][idx] - GSC_Mag ) )
        guessdistmed = np.median(guessdistances[mask])
        # Update mask to ignore values with large STDEVS
        mask = np.logical_and( d2d.value < dist_value, guessdistances < 5 * guessdistmed )
        self.log.debug('Median of distance to guess = %f, Mask length = %d' %
                       ( guessdistmed, np.sum(mask) ) )
        # Solve linear equation
        result = scipy.optimize.minimize(nll, [1, b_ml0],
                                         args=(GSC_Mag[mask],
                                               seo_Mag[seo_SN][idx][mask],
                                               eps_data[mask]))
        m_ml, b_ml = result["x"]
        self.log.info('Fitted offset is %f mag, fitted slope is %f' % (b_ml, m_ml) )
        b_ml_corr = b_ml + (m_ml-1) * np.median(GSC_Mag[mask])
        self.log.info('Corrected offset is %f mag' % b_ml_corr)
        '''
        ### Make table with all data from source extractor
        # Collect data columns
        cols = []
        num = np.arange(1, len(objects['x']) + 1 )
        cols.append(fits.Column(name='ID', format='D',
                                array=num))
        cols.append(fits.Column(name='X', format='D',
                                array=objects['x'][seo_SN],
                                unit='pixel'))
        cols.append(fits.Column(name='Y', format='D',
                                array=objects['y'][seo_SN],
                                unit='pixel'))
        cols.append(fits.Column(name='Uncalibrated Magnitude', format='D',
                                array=seo_Mag,
                                unit='magnitude'))
        cols.append(fits.Column(name='Uncalibrated Magnitude_Err', format='D',
                                array=seo_MagErr, unit='magnitude'))
        # Make table
        c = fits.ColDefs(cols)
        sources_table = fits.BinTableHDU.from_columns(c)
        '''
        ### Make table with data which was fit
        # Collect data columns
        cols = []
        cols.append(fits.Column(name='RA', format='D', array=GSC_RA[mask],
                                unit='deg'))
        cols.append(fits.Column(name='Dec', format='D', array=GSC_DEC[mask],
                                unit='deg'))
        cols.append(fits.Column(name='Diff_Deg', format='D', array=d2d[mask],
                                unit='deg'))
        cols.append(fits.Column(name='GSC_Mag', format='D',
                                array=GSC_Mag[mask], unit='magnitude'))
        cols.append(fits.Column(name='Img_Mag', format='D',
                                array=seo_Mag[seo_SN][idx][mask],
                                unit='magnitude'))
        cols.append(fits.Column(name='Error', format='D', array=eps_data[mask],
                                unit='magnitude'))
        # Make table
        c = fits.ColDefs(cols)
        fitdata_table = fits.BinTableHDU.from_columns(c)
        '''
        ### Make output data
        # Copy data from datain
        self.dataout = self.datain
        '''
        # Add Photometric Zero point magnitude
        self.dataout.setheadval('PHTZPRAW', -b_ml_corr, 'Photometric zeropoint for RAW data')
        self.dataout.setheadval('PTZRAWER', 0.0, 'Uncertainty of the RAW photometric zeropoint')
        self.dataout.setheadval('PHOTZP', 8.9,  'Photometric zeropoint MAG=-2.5*log(data)+PHOTZP')
        self.dataout.setheadval('BUNIT', 'Jy/pixel', 'Units for the data')
        '''
        # Scale the image using calculated b_ml_corr
        #image_background = fits.open(bkgdfilename)[0].data
        #bzero = np.nanpercentile(self.dataout.image,self.getarg('zeropercent'))
        #bzero = image_background
        #-- Alternative bzero idea:
        #-mask = image_array < np.percentile(image,90)
        #-bzero = np.median(image_array[mask])
        #bscale = 3631. * 10 ** (b_ml_corr/2.5)
        #self.dataout.image = bscale * (self.dataout.image - bzero)
        # Add sources and fitdata table
        self.dataout.tableset(sources_table.data,'Sources',sources_table.header)
        
        #self.dataout.tableset(fitdata_table.data,'Fit Data',fitdata_table.header)
        

        '''
        ### If requested make a plot of the fit and save as png
        if self.getarg('fitplot'):
            # Set up plot
            plt.figure(figsize=(10,7))
            # Plot 5sigma error range
            gmin = min(GSC_Mag[mask])
            gmax = max(GSC_Mag[mask])
            plt.fill([gmin,gmin,gmax,gmax],[gmin+b_ml0-guessdistmed, gmin+b_ml0+guessdistmed,
                                            gmax+b_ml0+guessdistmed, gmax+b_ml0-guessdistmed],'c')
            # Plot fits
            plt.plot(GSC_Mag[mask],m_ml*GSC_Mag[mask]+b_ml)
            plt.plot(GSC_Mag[mask],GSC_Mag[mask]+b_ml0)
            # Plot the datapoints
            plt.errorbar(GSC_Mag[d2d.value<dist_value],seo_Mag[seo_SN][idx][d2d.value<dist_value],
                         yerr=np.sqrt(eps_data[d2d.value<dist_value]**2),fmt='o',linestyle='none')
            plt.errorbar(GSC_Mag[mask],seo_Mag[seo_SN][idx][mask],
                         yerr=np.sqrt(eps_data[mask]**2),fmt='o',linestyle='none')
            #plt.plot(GSC_Mag[d2d.value<dist_value],m_ml*GSC_Mag[d2d.value<dist_value]+zeropoint_fit[1])
            plt.legend(['LM-fit','Fit-Guess','GuessDistMed Range','d<distval Data','Good Data'])
            plt.ylabel('Source extrator magnitude')
            plt.xlabel('Star catalog magnitude')
            plt.title('Calibration Fit for file\n' + os.path.split(self.dataout.filename)[1])
            # Plot the fit
            # Axis and labels
            # Save the image
            pngname = self.dataout.filenamebegin + 'FCALplot.png'
            plt.savefig(pngname)
            self.log.debug('Saved fit plot under %s' % pngname)
            '''
        ### If requested make a text file with the sources list
        if self.getarg('sourcetable'):

            # Save region file

            filename = self.dataout.filenamebegin + 'FCALsources.reg'
            with open(filename, 'w+') as f:
                f.write("# Region file format: DS9 version 4.1\n")
                f.write("""global color=green dashlist=8 3 width=1 font="helvetica 10 normal roman" select=1 highlite=1 dash=0 fixed=0 edit=1 move=1 delete=1 include=1 source=1 image\n""")
                for i in range(len(seo_catalog['x'][seo_SN])):
                    f.write("circle(%.7f,%.7f,0.005) # text={%i}\n"%(seo_catalog['x'][seo_SN][i],seo_catalog['y'][seo_SN][i],num[i]))

            # Save the table
            txtname = self.dataout.filenamebegin + 'FCALsources.txt'
            ascii.write(self.dataout.tableget('Sources'),txtname,
                        format = self.getarg('sourcetableformat'))
            self.log.debug('Saved sources table under %s' % txtname)

'''
def residual(params, x, data, errors):
    """ Fitting function for lmfit
    """
    m, c = params
    model = m*x+c
    inv_sigma2 = 1.0/(errors**2)
    #print(m,c,-0.5*(np.sum(((data-model)**2)*inv_sigma2)))
    return -0.5*(np.sum(((data-model)**2)*inv_sigma2))
'''

if __name__ == '__main__':
    """ Main function to run the pipe step from command line on a file.
        Command:
        python stepparent.py input.fits -arg1 -arg2 . . .
        Standard arguments:
        --config=ConfigFilePathName.txt : name of the configuration file
        --test : runs the functionality test i.e. pipestep.test()
        --loglevel=LEVEL : configures the logging output for a particular level
    """
    StepSrcExtPy().execute()

'''HISTORY:
2018-09-019 - Started based on Amanda's code. - Marc Berthoud
'''
