from django.db import transaction
from django.shortcuts import redirect, render_to_response, HttpResponse
from django.http import Http404,HttpResponseRedirect
from django.template import RequestContext
from django.db import connection
from django.utils import simplejson as json
from customforms.models import *
from esp.program.models import Program
from esp.customforms.DynamicModel import DynamicModelHandler as DMH
from esp.customforms.DynamicForm import FormHandler
from esp.customforms.linkfields import cf_cache
from django.contrib.contenttypes.models import ContentType

from esp.users.models import ESPUser
from esp.middleware import ESPError

def landing(request):
    if request.user.is_authenticated():
        forms=Form.objects.filter(created_by=request.user)
        return render_to_response("customforms/landing.html", {'form_list':forms}, context_instance=RequestContext(request))
    return HttpResponseRedirect('/')    

def formBuilder(request):
    if request.user.is_authenticated():
        prog_list=Program.objects.all()
        form_list=Form.objects.filter(created_by=request.user)
        return render_to_response('customforms/index.html',{'prog_list':prog_list, 'form_list':form_list, 'only_fkey_models':cf_cache.only_fkey_models.keys()}) 
    return HttpResponseRedirect('/')
    
def formBuilderData(request):
    if request.is_ajax():
        if request.method=='GET':
            data={}
            data['only_fkey_models']=cf_cache.only_fkey_models.keys()
            data['link_fields']={}
            for category, category_info in cf_cache.link_fields.items():
                data['link_fields'][category]={}
                data['link_fields'][category].update(category_info['fields'])
            
            return HttpResponse(json.dumps(data))
    return HttpResponse(status=400)                
    
def getPerms(request):
    """
    Returns the various permissions available for the current program via AJAX.
    """
    if request.is_ajax():
        if request.method=='GET':
            try:
                prog_id=int(request.GET['prog_id'])    
            except ValueError:
                return HttpResponse(status=400)
            prog=Program.objects.get(pk=prog_id)
            perms={'teachers':[], 'students':[]}
            for module in prog.getModules(None):
                teach_desc=module.teacherDesc()
                stud_desc=module.studentDesc()
                if teach_desc:
                    for k,v in teach_desc.items():
                        perms['teachers'].append([k,v])
                elif stud_desc:
                    for k,v in stud_desc.items():
                        perms['students'].append([k,v])
            return HttpResponse(json.dumps(perms))
    return HttpResponse(status=400)                                        

@transaction.commit_on_success
def onSubmit(request):
    #Stores form metadata in the database.
    
    if request.is_ajax():
        if request.method == 'POST':
            metadata=json.loads(request.raw_post_data)
            
            fields=[]
            
        #Creating form
        form=Form.objects.create(title=metadata['title'], 
            description=metadata['desc'], created_by=request.user, link_type=metadata['link_type'], 
            link_id=int(metadata['link_id']), anonymous=metadata['anonymous'], perms=metadata['perms'],
            success_message=metadata['success_message'], success_url=metadata['success_url'])
        
        #Inserting pages
        for page in metadata['pages']:
            new_page=Page.objects.create(form=form, seq=int(page['seq']))
            
            #inserting sections
            for section in page['sections']:
                new_section=Section.objects.create(page=new_page, title=section['data']['question_text'], 
                    description=section['data']['help_text'], seq=int(section['data']['seq']))
                
                #inserting fields
                for field in section['fields']:
                    new_field=Field.objects.create(form=form, section=new_section, field_type=field['data']['field_type'], 
                        seq=int(field['data']['seq']), label=field['data']['question_text'], help_text=field['data']['help_text'], 
                        required=field['data']['required'])
                    
                    fields.append( (new_field.id, new_field.field_type) ) 
                    
                    #inserting other attributes, if any
                    for atype, aval in field['data']['attrs'].items():
                        new_attr=Attribute.objects.create(field=new_field, attr_type=atype, value=aval)
                        
        dynH=DMH(form=form, fields=fields)
        dynH.createTable()                
                            
        return HttpResponse('OK')

def get_or_create_altered_obj(model, initial_id, **attrs):
    if model.objects.filter(id=initial_id).exists():
        obj = model.objects.get(id=initial_id)
        obj.__dict__.update(attrs)
        obj.save()
        created = False
    else:
        obj = model.objects.create(**attrs)
        created = True
    return (obj, created)
    
def get_new_or_altered_obj(*args, **kwargs):
    return get_or_create_altered_obj(*args, **kwargs)[0]

@transaction.commit_on_success        
def onModify(request):
    """
    Handles form modifications
    """
    if request.is_ajax():
        if request.method=='POST':
            metadata=json.loads(request.raw_post_data)
            try:
                form=Form.objects.get(id=int(metadata['form_id']))
            except:
                raise ESPError(False), 'Form %s not found' % metadata['form_id']
            dmh=DMH(form=form)
            link_models_list=[]     # Stores a cache of link models that should not be removed
            
            #Populating the old fields list
            dmh._getModelFieldList()
            
            #Check if only_fkey links have changed
            if form.link_type!=metadata['link_type']:
                dmh.change_only_fkey(form.link_type, metadata['link_type'])
            
            # NOT updating 'anonymous'
            form.__dict__.update(title=metadata['title'], description=metadata['desc'], link_type=metadata['link_type'], 
                link_id=int(metadata['link_id']), perms=metadata['perms'],
                success_message=metadata['success_message'], success_url=metadata['success_url'])
            
            form.save()
            curr_keys={'pages':[], 'sections':[], 'fields':[]}
            old_pages=Page.objects.filter(form=form)
            old_sections=Section.objects.filter(page__in=old_pages)    
            old_fields=Field.objects.filter(form=form)
            for page in metadata['pages']:
                curr_page = get_new_or_altered_obj(Page, page['parent_id'], form=form, seq=int(page['seq']))
                curr_keys['pages'].append(curr_page.id)
                for section in page['sections']:
                    curr_sect = get_new_or_altered_obj(Section, section['data']['parent_id'], page=curr_page, title=section['data']['question_text'], description=section['data']['help_text'], seq=int(section['data']['seq']))
                    curr_keys['sections'].append(curr_sect.id)
                    for field in section['fields']:
                        (curr_field, field_created) = get_or_create_altered_obj(Field, field['data']['parent_id'], 
                                                    form=form, section=curr_sect, field_type=field['data']['field_type'], 
                                                    seq=int(field['data']['seq']), label=field['data']['question_text'], 
                                                    help_text=field['data']['help_text'], required=field['data']['required'])
                        if field_created:
                            # Check for link field
                            if cf_cache.isLinkField(curr_field.field_type):
                                dmh.addLinkFieldColumn(curr_field)
                            else: dmh.addField(curr_field)
                        elif not cf_cache.isLinkField(curr_field.field_type):
                            dmh.updateField(curr_field)
                            
                        # Store a reference to the linked model so that we don't drop it from the table.
                        if cf_cache.isLinkField(curr_field.field_type):
                            model_cls=cf_cache.modelForLinkField(curr_field.field_type)
                            if model_cls.__name__ not in link_models_list: link_models_list.append(model_cls.__name__)
                            
                        for atype, aval in field['data']['attrs'].items():
                            curr_field.set_attribute(atype, aval)
                        curr_keys['fields'].append(curr_field.id)
                        
            del_fields=old_fields.exclude(id__in=curr_keys['fields'])
            for df in del_fields:
                # Check for link fields
                if cf_cache.isLinkField(df.field_type):
                    model_cls=cf_cache.modelForLinkField(df.field_type)
                    if model_cls.__name__ not in link_models_list:
                        # This column needs to be dropped
                        dmh.removeLinkField(df)
                else:        
                    dmh.removeField(df)
            del_fields.delete()
                
            old_sections.exclude(id__in=curr_keys['sections']).delete()
            old_pages.exclude(id__in=curr_keys['pages']).delete()
            
            return HttpResponse('OK')                                                 
                    
def hasPerm(user, form):
    """
    Checks if this user qualifies to view this form
    """
    if (not form.anonymous or form.perms!="") and not user.is_authenticated():
        return False, "You need to be logged in to view this form."
    if form.perms=="":
        return True, ""
    else:
        perms_list=form.perms.strip(',').split(',')
        main_perm=perms_list[0]
        prog_id=""
        sub_perms=None
        if len(perms_list)>1:
            prog_id=perms_list[1]
            if len(perms_list)>2:
                sub_perms=perms_list[2:]
        Qlist=[]
        Qlist.append(ESPUser.getAllOfType(main_perm))  #Check -> what to do with students?
        if sub_perms:
            if prog_id!="":
                prog=Program.objects.get(pk=int(prog_id))
                all_Qs=prog.getLists(QObjects=True)
                for perm in sub_perms:
                    Qlist.append(all_Qs[perm]['list'])
        if ESPUser.objects.filter(id=user.id).filter(*Qlist).exists():
            return True, ""
        else:
            return False, "You are not permitted to view this form."                    
        
        
def viewForm(request, form_id):
    """Form viewing and submission"""
    try:
        form_id=int(form_id)
    except ValueError:
        raise Http404
        
    form=Form.objects.get(pk=form_id)
    
    perm, error_text=hasPerm(request.user, form)
    if not perm:
        return render_to_response('customforms/error.html', {'error_text': error_text}, context_instance=RequestContext(request))    
    fh=FormHandler(form=form, request=request, user=request.user)
    wizard=fh.getWizard()
    extra_context={'form_title':form.title, 'form_description':form.description}
    return wizard(request, extra_context=extra_context)    

def success(request, form_id):
    """
    Successful form submission
    """
    try:
        form_id=int(form_id)
    except ValueError:
        raise Http404
    
    form=Form.objects.get(pk=form_id)        
    return render_to_response('customforms/success.html',{'success_message': form.success_message, 
                                                            'success_url': form.success_url}, 
                                                            context_instance=RequestContext(request))

def viewResponse(request, form_id):
    """
    Viewing response data
    """
    if request.user.is_authenticated:
        try:
            form_id=int(form_id)
        except ValueError:
            raise Http404
        form=Form.objects.get(id=form_id)
        return render_to_response('customforms/view_results.html', {'form':form})
    else:
        return HttpResponseRedirect('/')
        
def getExcelData(request, form_id):
    """
    Returns the response data as an excel spreadsheet
    """
    
    try:
        form_id=int(form_id)
    except ValueError:
        return HttpResponse(status=400)
        
    form=Form.objects.get(pk=form_id)
    fh=FormHandler(form=form, request=request)
    wbk=fh.getResponseExcel()
    response=HttpResponse(wbk.getvalue(), mimetype="application/vnd.ms-excel")
    response['Content-Disposition']='attachment; filename=%s.xls' % form.title
    return response                   
        
def getData(request):
    """
    Returns response data via Ajax
    """
    if request.is_ajax():
        if request.method=='GET':
            try:
                form_id=int(request.GET['form_id'])
            except ValueError:
                return HttpResponse(status=400)
            form=Form.objects.get(pk=form_id)    
            fh=FormHandler(form=form, request=request)
            resp_data=json.dumps(fh.getResponseData(form))
            return HttpResponse(resp_data)
    return HttpResponse(status=400)
        
def getRebuildData(request):
    """
    Returns form metadata for rebuilding via AJAX
    """
    if request.is_ajax():
        if request.method=='GET':
            try:
                form_id=int(request.GET['form_id'])
            except ValueError:
                return HttpResponse(status=400)
            form=Form.objects.get(pk=form_id)
            fh=FormHandler(form=form, request=request)
            metadata=json.dumps(fh.rebuildData())
            return HttpResponse(metadata)
    return HttpResponse(status=400)    
    
def get_links(request):
    """
    Returns the instances for the specified model, to link to in the form builder.
    """
    if request.is_ajax():
        if request.method=='GET':
            try:
                link_model=cf_cache.only_fkey_models[request.GET['link_model']]
            except KeyError:
                try:
                    link_model=cf_cache.link_fields[request.GET['link_model']]['model']
                except KeyError:
                    return HttpResponse(status=400)    
            link_objects=link_model.objects.all()        
            retval={}
            for obj in link_objects:
                retval[obj.id]=unicode(obj)
                
            return HttpResponse(json.dumps(retval))
    return HttpResponse(status=400)                                        
        
def test():
    f=Form.objects.get(pk=16)
    from esp.customforms.DynamicForm import rd
    dyn=DMH(form=f).createDynModel()
    rd.depend_on_model(lambda:dyn)
    rd.run_all_delayed()
    data1=rd(dyn,f)
    user=ESPUser.objects.get(pk=1)
    dyn.objects.create(user=user, question_83='yyy')
    data2=rd(dyn,f)
    return {'first':data1, 'second':data2}
    
    
            
