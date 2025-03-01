import os
import ogr
import osr
import gdal
from .inspectors import GDALInspector, OGRInspector
from .utils import FileTypeNotAllowed, GdalErrorHandler, load_handler, launder, increment
from .handlers import IMPORT_HANDLERS
from django import db

ogr.UseExceptions()


class Import(object):
    _import_handlers = []
    handler_results = []
    file_extensions = ['shp', 'zip']

    def filter_handler_results(self, handler_name):
        return filter(lambda results: handler_name in results.keys(), self.handler_results)

    def _initialize_handlers(self):
        self._import_handlers = [load_handler(handler, self)
                                 for handler in IMPORT_HANDLERS]

    @property
    def import_handlers(self):
        """
        Initializes handlers and/or returns them.
        """
        if not self._import_handlers:
            self._initialize_handlers()

        return self._import_handlers

    def import_file(self, filename, **kwargs):
        raise NotImplementedError

    def file_extension_not_allowed(self, request, *args, **kwargs):
        raise FileTypeNotAllowed

    def handle(self, configuration_options, *args, **kwargs):
        """
        Executes the entire import process.
        1) Imports the dataset from the source dataset to the target.
        2) Executes arbitrary handlers that can modify the data set.
        3) Executes arbitrary publish handlers to publish the data set.
        """

        layers = self.import_file(configuration_options=configuration_options)

        for layer, config in layers:
            config['handler_results'] = self.run_import_handlers(layer, config)

        return layers

    def run_import_handlers(self, layer, layer_config, *args, **kwargs):
        """
        Handlers that are run on each layer of a data set.
        """
        self.handler_results = []

        for handler in self.import_handlers:
            self.handler_results.append({type(handler).__name__ : handler.handle(layer, layer_config, *args, **kwargs)})

        return self.handler_results


class GDALImport(Import):

    source_inspectors = [GDALInspector]
    target_inspectors = [OGRInspector]

    def __init__(self, filename, target_store=None):
        self.file = filename
        self.completed_layers = []

        if target_store is None:
            d = db.connections['datastore'].settings_dict
            connection_string = "PG:dbname='%s' user='%s' password='%s' host='%s' port='%s'" % (d['NAME'], d['USER'],
                                                                                                d['PASSWORD'],
                                                                                                d['HOST'], d['PORT'])
            self.target_store = connection_string

    def open_datastore(self, connection_string, inspectors, *args, **kwargs):
        """
        Opens the source source data set using GDAL.
        """

        for inspector in inspectors:
            data = inspector(connection_string, *args, **kwargs).open()
            if data is not None:
                return data

    def open_source_datastore(self, connection_string, *args, **kwargs):
        """
        Opens the source source data set using GDAL.
        """

        return self.open_datastore(connection_string, self.source_inspectors, *args, **kwargs)

    def open_target_datastore(self, connection_string, *args, **kwargs):
        """
        Opens the target data set using OGR.
        """

        return self.open_datastore(connection_string, self.target_inspectors, *args, **kwargs)

    def create_target_dataset(self, target_datastore, layer_name, *args, **kwargs):
        """
        Creates the data source in the target data store.
        """
        return target_datastore.CreateLayer(layer_name, *args, **kwargs)

    def get_layer_type(self, layer, source):
        """
        A hook for returning the GeometryType of a layer.

        This is work around for a limitation of the Shapefile: when reading a Shapefile of type
        SHPT_ARC, the corresponding layer will be reported as of type wkbLineString, but depending on the number
        of parts of each geometry, the actual type of the geometry for each feature can be either OGRLineString
        or OGRMultiLineString. The same applies for SHPT_POLYGON shapefiles, reported as layers of type wkbPolygon,
        but depending on the number of parts of each geometry, the actual type can be either OGRPolygon or
        OGRMultiPolygon.
        """
        if source.GetDriver().ShortName == 'ESRI Shapefile':
            geom_type = layer.GetGeomType()

            # If point return MultiPoint
            if geom_type == 1:
                return 4

            # If LineString return MultiLineString
            if geom_type == 2:
                return 5

            # if Polygon return MutliPolygon
            if geom_type == 3:
                return 6

        return layer.GetGeomType()

    def import_file(self, *args, **kwargs):
        """
        Loads data that has been uploaded into whatever format we need for serving.
        """
        filename = self.file
        self.completed_layers = []
        err = GdalErrorHandler()
        gdal.PushErrorHandler(err.handler)
        configuration_options = kwargs.get('configuration_options', [{'index': 0}])

        # Configuration options should be a list at this point since the importer can process multiple layers in a
        # single import
        if isinstance(configuration_options, dict):
            configuration_options = [configuration_options]

        data = self.open_source_datastore(filename, *args, **kwargs)
        target_file = self.open_target_datastore(self.target_store)

        target_create_options = []

        # Prevent numeric field overflow for shapefiles https://trac.osgeo.org/gdal/ticket/5241
        if target_file.GetDriver().GetName() == 'PostgreSQL':
            target_create_options.append('PRECISION=NO')

        for layer_options in configuration_options:
            layer = data.GetLayer(layer_options.get('index'))
            layer_name = layer_options.get('name', layer.GetName().lower())
            layer_type = self.get_layer_type(layer, data)
            srs = layer.GetSpatialRef()

            if layer_name == 'ogrgeojson':
                try:
                    layer_name = os.path.splitext(os.path.basename(filename))[0].lower()
                except IndexError:
                    pass

            layer_name = launder(str(layer_name))

            # default the layer to 4326 if a spatial reference is not provided
            if not srs:
                srs = osr.SpatialReference()
                srs.ImportFromEPSG(4326)

            n = 0
            while True:
                n += 1
                try:
                    target_layer = self.create_target_dataset(target_file, layer_name, srs,
                                                              layer_type, options=target_create_options)
                except RuntimeError as e:
                    # the layer already exists in the target store, increment the name
                    if 'Use the layer creation option OVERWRITE=YES to replace it.' in e.message:
                        layer_name = increment(layer_name)

                        # try 100 times to increment then break
                        if n >= 100:
                            break

                        continue
                    else:
                        raise e
                break

            # adding fields to new layer
            layer_definition = ogr.Feature(layer.GetLayerDefn())

            for i in range(layer_definition.GetFieldCount()):
                target_layer.CreateField(layer_definition.GetFieldDefnRef(i))

            for i in range(0, layer.GetFeatureCount()):
                feature = layer.GetFeature(i)

                if feature:
                    if feature.geometry().GetGeometryType() != target_layer.GetGeomType() and \
                        target_layer.GetGeomType() in range(4, 7):

                        conversion_function = ogr.ForceToMultiPolygon

                        if target_layer.GetGeomType() == 5:
                            conversion_function = ogr.ForceToMultiLineString

                        elif target_layer.GetGeomType() == 4:
                            conversion_function = ogr.ForceToMultiPoint

                        geom = ogr.CreateGeometryFromWkb(feature.geometry().ExportToWkb())
                        feature.SetGeometry(conversion_function(geom))
                    target_layer.CreateFeature(feature)

            self.completed_layers.append([target_layer.GetName(), layer_options])

        return self.completed_layers
