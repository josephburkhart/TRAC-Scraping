"""
This file contains a class that can collate multi-axis data from one of 
a set number of TRAC webpages.

There are two kinds of TRAC webpages:
    - link-based tables
    - object-based tables

Of these two kinds, either can have a variant in which year is broken into its
own drop-down menu.
"""

# Browser-agnostic imports
from pathlib import Path
import os
from typing import Literal, Optional
import pandas as pd
from time import sleep
from tqdm import tqdm
import json
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# Browser-specific imports
# TODO: conditional import based on user's choice of browser
from selenium.webdriver import Firefox                  
from selenium.webdriver.firefox.options import Options

# WEBDRIVER_PATH = None
WEBPAGE_TYPES = {
    'https://trac.syr.edu/phptools/immigration/ntanew/': 'object-whole',
    'https://trac.syr.edu/phptools/immigration/closure/': 'object-whole',
    'https://trac.syr.edu/phptools/immigration/asyfile/': 'object-whole',
    'https://trac.syr.edu/phptools/immigration/asylum/': 'object-whole',
    'https://trac.syr.edu/phptools/immigration/mpp4/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/juvenile/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/mwc/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/cbparrest/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/cbpinadmiss/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/arrest/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/detainhistory/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/remove/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/removehistory/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/secure/': 'link-whole',
    'https://trac.syr.edu/phptools/immigration/backlog/': 'object-broken',
    'https://trac.syr.edu/phptools/immigration/addressrep/': 'map-table',
    'https://trac.syr.edu/immigration/reports/judgereports/': 'table-only-1',
    'https://trac.syr.edu/phptools/immigration/asylumbl/': 'object-broken',
    'https://trac.syr.edu/phptools/immigration/bond/': 'table-tab',
    'https://trac.syr.edu/phptools/immigration/detention/': 'link-broken',
    'https://trac.syr.edu/immigration/detentionstats/facilities.html': 'table-only-2',
    'https://trac.syr.edu/immigration/detentionstats/atd_pop_table.html': 'table-only-2'
}

FULLY_SUPPORTED_TYPES = ['object-whole', 'link-whole']
PARTIALLY_SUPPORTED_TYPES = ['object-broken', 'link-broken']

TIMEOUT = 10

class DatasetException(Exception):
    def __init__(self, dump_file, current_t1_row, current_t2_row):
        self.dump_file = dump_file
        self.current_t1_row = current_t1_row
        self.current_t2_row = current_t2_row

class Table:
    """A Table is a collection of Rows."""
    def __init__(self, web_element, table_type: Literal['object', 'link'], index):
        # Set instance attributes
        self.web_element = web_element
        self._rows = None
        self.table_type = table_type
        self.index = index

    def rows(self, recalculate=False, driver=None, webpage_type=None):
        # Only calculate rows when asked to
        # https://stackoverflow.com/a/69379239/15426433
        # Note: if recalculate is True, driver and webpage_type are required
        if self._rows is None or recalculate:
            self._rows = self.calculate_rows(refresh_table=recalculate,
                                             driver=driver,
                                             webpage_type=webpage_type)
        return self._rows
    
    def calculate_rows(self, refresh_table: bool=False, driver=None, webpage_type=None):
        # If table has been refreshed, calculate a new web element
        # Note: if refresh_table is True, driver and webpage_type are required
        if refresh_table:
            table_elements = Table.calculate_web_elements(driver, webpage_type)
            self.web_element = table_elements[self.index]
        # Get elements for all rows
        wait = WebDriverWait(self.web_element, TIMEOUT)
        if self.table_type == 'object':
            row_elements = wait.until(EC.presence_of_all_elements_located(
                    (By.CLASS_NAME, 'flex-row')
                )
            )
        elif self.table_type == 'link':
            row_elements = wait.until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, ".//tr")
                )
            )

        # Filter out meaningless elements
        def is_meaningful(text):
            return (text != '' and 'All' not in text and 'Total' not in text)
        text_rows = self.web_element.text.split('\n')

        text_indices_to_skip = [i for i, r in enumerate(text_rows) if not is_meaningful(r)]
        text_rows = [r for i, r in enumerate(text_rows) if i not in text_indices_to_skip]
        n_elements_to_skip = len(row_elements) - len(text_rows)
        row_elements = row_elements[n_elements_to_skip:]                  # currently, the skippable elements are always at the beginning, but this could change later

        # Make a Row from each element
        return [Row(e, t, self.table_type) for e, t in zip(row_elements, text_rows)]
    
    @staticmethod
    def calculate_all(driver, webpage_type):
        # Helper method that just calls calculate but in a more friendly way
        if 'object' in webpage_type:
            table_type = 'object'
        elif 'link' in webpage_type:
            table_type = 'link'
        table_elements = Table.calculate_web_elements(driver, webpage_type)
        return [Table(e, table_type, i) for i, e in enumerate(table_elements)]
    
    @staticmethod
    def calculate_web_elements(driver, webpage_type):
        # Calculate a single Table for a given index
        # If index is None, all Tables will be returned
        if 'object' in webpage_type:
            class_name = 'table-fixed'
        elif 'link' in webpage_type:
            class_name = 'Table'

        wait = WebDriverWait(driver, TIMEOUT)
        table_elements = wait.until(     
            EC.presence_of_all_elements_located((By.CLASS_NAME, class_name))
        )
        return table_elements

class Row:
    """A Row is clickable, and has a name and a value."""
    def __init__(self, web_element, text, table_type: Literal['object', 'link']):
        # Set instance attributes
        self.table_type = table_type
        self.web_element = web_element
        self.name, self.value = text.rsplit(' ', 1)
        self.value = int(self.value.replace(',', ''))
    
    def click(self):
        if self.table_type == 'object':
            self.web_element.click()
        elif self.table_type == 'link':
            wait = WebDriverWait(self.web_element, TIMEOUT)
            clickable_element = wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, ".//td[@class='Data l']/a")
                )
            )
            clickable_element.click()

class AxisMenu:
    """An AxisMenu is a clickable collection of Options."""
    def __init__(self, driver, webpage_type, axis_index):
        # Set instance attributes
        self.driver = driver
        self.webpage_type = webpage_type

        # Calculate menus
        wait = WebDriverWait(driver, TIMEOUT)
        #TODO: account for object-broken and link-broken
        if 'object' in webpage_type:
            menus = wait.until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, "//button[starts-with(@id, 'headlessui-listbox-button')]")
                )
            )
        elif 'link' in webpage_type:
            menus = wait.until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, "//select[starts-with(@id, 'dimension_pick')]")
                )
            )
        self.clickable_element = menus[axis_index]

        # Calculate options
        self.calculate_options()

    def calculate_options(self):
        if 'object' in self.webpage_type:
            # Listbox is not contained in the clickable element, and options
            # are inside it. The clickable element must be clicked before
            # the listbox will appear.
            self.click()
            wait = WebDriverWait(self.driver, TIMEOUT)
            listbox_element = wait.until(EC.presence_of_element_located(
                (By.XPATH, "//ul[starts-with(@id, 'headlessui-listbox-options')]")
            ))

            wait = WebDriverWait(listbox_element, TIMEOUT)
            option_elements = wait.until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, ".//*[@role='option']/li/span")
                )
            )

        elif 'link' in self.webpage_type:
            # options are inside the clickable element
            wait = WebDriverWait(self.clickable_element, TIMEOUT)
            option_elements = wait.until(EC.presence_of_all_elements_located(
                    (By.XPATH, f".//option")
            ))
        self.options = [Option(e) for e in option_elements]

    def click(self):
        self.clickable_element.click()

    def set_to(self, axis_name: str):
        # For object-based, have to re-calculate options because the references
        # calculated previously have turned stale
        if 'object' in self.webpage_type:
            self.calculate_options()        # note: this automatically clicks
        elif 'link' in self.webpage_type:
            self.click()                    # opens the menu

        # Select the given option
        # TODO: is there a better way to do this than list comprehension?
        option_to_click = [o for o in self.options if o.name == axis_name]
        option_to_click[0].click()

    def calculate_all(driver, webpage_type):
        if webpage_type == 'object-whole':
            menus = [AxisMenu(driver, webpage_type, i) for i in range(3)]
        elif webpage_type == 'link-whole':
            menus = [AxisMenu(driver, webpage_type, i) for i in range(3)]
        elif webpage_type == 'object-broken':
            pass        #TODO: add this
        elif webpage_type == 'link-broken':
            pass        #TODO: add this
        return menus
    
    @property
    def option_names(self):
        return [o.name for o in self.options]

class Option:
    """An Option is clickable and has a name."""
    def __init__(self, clickable_web_element):
        self.clickable_element = clickable_web_element
        self.name = clickable_web_element.text
    
    def click(self):
        self.clickable_element.click()


class CollationEngine():
    # For first implementation, user will specify three axes which will correspond
    # to the axes selected in the browser for the three tables (left to right).
    # In future versions, I want the user to be able to specify an arbitrary number
    # of axes, and have the engine construct a dataset that includes all of them.
    def __init__(self, url: str, filename: str | Path, axes: list[str], headless: bool=False):
        # Check for valid filename type
        if type(filename) not in (str, type(Path())):
            raise TypeError(f"filename must be of type str or Path")

        # Make path of output file absolute
        filename = Path(filename)
        if not filename.is_absolute():
            filename = filename.resolve()

        # Check that we have permission to write the output file
        testfilename = filename.parent / 'test.txt'
        try:
            testfile = open(testfilename, 'w')
        except (OSError, IOError):
            msg = f"Error: Cannot write a file to the folder {filename.parent}."
            msg += "\nPlease enter a different value for `filename`."
            print(msg)
            quit()
        else:
            testfile.close()
            try:
                os.remove(testfilename)
            except OSError:
                msg = f"Warning: temporary file could not be deleted: {testfilename}"
                msg += "\nPlease delete file manually after execution is complete."
                print(msg)
        
        # Check for valid URL
        if WEBPAGE_TYPES[url] not in FULLY_SUPPORTED_TYPES:
            if WEBPAGE_TYPES[url] in PARTIALLY_SUPPORTED_TYPES:
                print("Warning: URL is not fully supported. Retrieving anyway...")
            elif url in WEBPAGE_TYPES.keys():
                raise ValueError("URL is not supported")
            else:
                raise ValueError("URL is not recognized")
            
        # Check for valid headless flag
        if type(headless) != bool:
            raise TypeError("headless must be of type bool")
        
        # Initialize Driver
        options = Options()
        if headless:
            options.add_argument('--headless')
        self.driver = Firefox(options=options)
        self.driver.get(url)
        
        # Set instance attributes
        self.filename = filename
        self.axes = axes
        self.tables = [None, None, None]

        # Determine webpage type
        self.webpage_type = WEBPAGE_TYPES[url]

        # Calculate Menus
        # TODO: this currently doesn't work for broken-out webpages
        self.menus = AxisMenu.calculate_all(self.driver, self.webpage_type)

        # Calculate tables
        # TODO: this currently doesn't work for broken-out webpages
        self.tables = Table.calculate_all(self.driver, self.webpage_type)

        # Check for valid input axis names
        # Note: technically, all menus should have the same options, but this
        #       will not always be the case if support is added for more webpage
        #       types, so all menus will be checked
        for m in self.menus:
            for a in self.axes:
                if a not in m.option_names:
                    raise ValueError(f"Axis name {a} could not be found")

        # Set Axes
        for i, a in enumerate(self.axes):
            self.menus[i].set_to(a)

        # Dataset
        current_t1_row = None
        current_t2_row = None
        dump_file = None
        while True:
            try:
                self.create_dataset(dump_file, current_t1_row, current_t2_row)
            except DatasetException as e:
                dump_file = e.dump_file
                current_t1_row = e.current_t1_row
                current_t2_row = e.current_t2_row

            else:
                break
        self.clean_dataset()
        self.save_dataset()
    
        # close browser
        sleep(10)
        self.driver.close()

    def create_dataset(self,
                       dump_file: Optional[str | Path] = None, 
                       current_t1_row: Optional[int | None] = None, 
                       current_t2_row: Optional[int | None] = None):
        """
        Create a dataset of nested dictionaries from the webpage.
        
        If called with its optional parameters, this method will initialize the 
        dataset from a json file specified by `dump_file`, and only
        get data from rows including and after `current_t1_row` (for Table 1)
        and `current_t2_row` for (Table 2).

        Raises DatasetException if a stale element reference is found.
        """
        # Set progress bar formatting
        pbar_format = "{desc}{percentage:3.0f}%|{bar:30}| {n_fmt}/{total_fmt}"

        # If data was dumped previously, initialize dataset from it, then
        # delete the dump file if possible
        if dump_file != None:
            with open(dump_file, 'r') as f:
                data = json.load(f)
            try:
                os.remove(dump_file)
            except OSError:
                msg = f"Warning: dump file could not be deleted: {dump_file}"
                msg += "\nPlease delete file manually after execution is complete."
                print(msg)
        else:
            data = {}
        
        # Calculate rows for table 1, skipping ones previously clicked if necessary
        t1_rows = self.tables[0].rows(recalculate=True, 
                                      driver=self.driver, 
                                      webpage_type=self.webpage_type)
        if current_t1_row != None:
            t1_rows = t1_rows[current_t1_row:]
        
        # Iterate over un-clicked table 1 rows
        pbar1 = tqdm(t1_rows, leave=False, bar_format=pbar_format)
        for i, t1_row in enumerate(pbar1):  #https://stackoverflow.com/a/45519268/15426433
            pbar1.set_description(shorten(f"Table 1: {t1_row.name}"))

            # Only initialize an empty dict if no table 2 rows were
            # previously clicked
            if t1_row.name not in data.keys():
                data[t1_row.name] = {}

            # Try clicking, and if unsuccessful then dump data to file and
            # pass row indices and dump file name to exception
            try:
                t1_row.click()
            except StaleElementReferenceException:  #TODO: put this in a method
                print('here')
                msg = f'\nEncountered a stale reference at {t1_row.name=}. '
                msg += 'Dumping data to file and restarting from current row.\n'
                print(msg)

                dump_file = f'data-up-to_T1-{t1_row.name}.json'
                with open(dump_file, 'w') as f:
                    json.dump(data, f)

                raise DatasetException(dump_file, i, None)

            # Calculate rows for table 2, skipping ones previously clicked if necessary
            t2_rows = self.tables[1].rows(recalculate=True, 
                                          driver=self.driver, 
                                          webpage_type=self.webpage_type)
            if current_t2_row != None:
                t2_rows = t2_rows[current_t2_row:]
            
            # Iterate over un-clicked table 2 rows
            pbar2 = tqdm(t2_rows, leave=False, bar_format=pbar_format)
            for j, t2_row in enumerate(pbar2):
                pbar2.set_description(shorten(f"Table 2: {t2_row.name}"))  
            
                # Try clicking, and if unsuccessful then dump data to file and
                # pass row indices and dump file name to exception
                try:
                    t2_row.click()
                except StaleElementReferenceException:  #TODO: put this in a method
                    msg = f'\nEncountered a stale reference at {t2_row.name=}. '
                    msg += 'Dumping data to file and restarting from current row.\n'
                    print(msg)

                    dump_file = f'data-up-to_T1-{t1_row.name}_T2-{t2_row.name}.json'
                    with open(dump_file, 'w') as f:
                        json.dump(data, f)

                    raise DatasetException(dump_file, i, j)

                # Copy rows from table 3 into the data dictionary
                t3_rows = self.tables[2].rows(recalculate=True,
                                              driver=self.driver,
                                              webpage_type=self.webpage_type)
                data[t1_row.name][t2_row.name] = {
                    r.name: r.value for r in t3_rows
                }
        
        # Save data as attribute and convert to dataframe
        self.data = data
        self.df = pd.concat(                # https://stackoverflow.com/a/54300940
            {k: pd.DataFrame(v).T for k, v in data.items()}, 
            axis=0
        )

    def clean_dataset(self):
        # Rectify missing second-level index entries
        unique_index1 = self.df.index.unique(0)
        unique_index2 = self.df.index.unique(1)
        new_index = pd.MultiIndex.from_product([unique_index1, unique_index2])
        self.df = self.df.reindex(new_index, axis='index')

        # Change all NaN values to 0
        self.df = self.df.fillna(value=0.0)

        # Sort df rows
        self.df = self.df.sort_index()

        # Sort df columns
        self.df = self.df.reindex(sorted(self.df.columns, axis=1))

        # Add a Total Column
        self.df['Total'] = self.df.sum(axis=1)

        # Convert all floats to int (cannot have fractions of people)
        float_cols = self.df.select_dtypes(include=['float64'])
        for col in float_cols.columns.values:
            self.df[col] = self.df[col].astype('int64')

        # Rename indices to reflect axis names
        for i, a in enumerate(self.axes):
            self.df.index.rename(name=a, level=i, inplace=True)     
            
    def save_dataset(self):
        self.df.to_hdf(self.filename, key='TRACDataset')

def shorten(text, 
            text_limit=24, 
            terminator='...', 
            delimiter='',
            padding=' ',
            pad_limit=25):
    """Shorten a string to the specified limit, adding additional padding."""
    if len(text) > text_limit:
        text = text[:text_limit - 1 - len(terminator)] + terminator + delimiter
    
    text += delimiter + (padding * (pad_limit - len(text)))
    return text


if __name__ == '__main__':
    engine = CollationEngine(
        url='https://trac.syr.edu/phptools/immigration/asylum/',
        filename='asylumdecisions.hdf',
        axes=['Month and Year of Decision', 'Nationality', 'Decision'],
        headless=True
    )