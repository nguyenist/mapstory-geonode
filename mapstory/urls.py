from django.conf import settings
from django.conf.urls import patterns
from django.conf.urls import url
from django.conf.urls.static import static
from django.contrib.auth.decorators import login_required
from django.views.generic import TemplateView
from geonode.urls import urlpatterns
from mapstory.views import IndexView
from mapstory.views import DiaryListView
from mapstory.views import DiaryCreateView
from mapstory.views import DiaryUpdateView

urlpatterns = patterns('',
    url(r'^$', IndexView.as_view()),
    url(r'^maps/new2$',
        'geonode.maps.views.new_map', {'template': 'maps/mapstory_map_view.html'},
        name='map-new2'),
    url(r'^maps/(?P<mapid>\d+)/view2$',
        'geonode.maps.views.map_view', {'template': 'maps/mapstory_map_view.html'},
        name='map-view2'),
    url(r'^diary$', DiaryListView.as_view(), name='diary'),
    url(r'^diary/write$', login_required(DiaryCreateView.as_view()), name='diary-create'),
    url(r'^diary/write/(?P<pk>\d+)$', login_required(DiaryUpdateView.as_view()), name='diary-update'),
    url(r'^getpage$', TemplateView.as_view(template_name='mapstory/getpage.html'), name='getpage'),
    url(r'^storylayerpage$', TemplateView.as_view(template_name='mapstory/storylayerpage.html'), name='storylayerpage'),
    url(r'^mapstorypage$', TemplateView.as_view(template_name='mapstory/mapstorypage.html'), name='mapstorypage'),
    url(r'^style-test$', TemplateView.as_view(template_name='testing/style_editor.html')),
) + urlpatterns

if settings.LOCAL_CONTENT:
    urlpatterns = static(settings.STATIC_URL + "assets", document_root=settings.LOCAL_ROOT + "/../../mapstory-assets", show_indexes=True) + urlpatterns

