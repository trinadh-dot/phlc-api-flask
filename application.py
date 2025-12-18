# """
# Entry point for the Flask app.

# Run:
#     pip install -r requirements.txt
#     set FLASK_APP=application.py   # on Windows (PowerShell: $env:FLASK_APP="application.py")
#     flask run --reload --port 5000

# API Endpoints (under /api):
#     POST /api/ingest/postgres          - Ingest Excel file into PostgreSQL
#     POST /api/ingest/postgres/from-s3  - Ingest from S3
#     POST /api/upload/s3                - Upload file(s) to S3
#     GET  /api/status/<job_id>          - Check ingestion job status
#     GET  /api/list_tables              - List DB tables
#     GET  /api/table_data/<table_name>  - Browse table data

# Admin UI:
#     Visit /admin/ for a Rails ActiveAdmin-style database explorer.

# Swagger-style API docs:
#     Visit /apidocs/ (provided by flasgger).
# """

# import os
# import sys
# import math

# from flask import Flask, redirect, url_for, render_template, request, abort
# from flasgger import Swagger
# from flask_admin import Admin, AdminIndexView, BaseView, expose
# from flask_admin.contrib.sqla import ModelView
# from sqlalchemy import inspect, Table, MetaData
# from sqlalchemy.orm import scoped_session

# # --- Ensure project root is first on sys.path ---
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# if BASE_DIR not in sys.path:
#     sys.path.insert(0, BASE_DIR)
# # ------------------------------------------------

# from app.db import init_db, engine, SessionLocal  # noqa: E402
# from app.models import Base, Job  # noqa: E402
# from app.api import api_bp  # noqa: E402
# from sqlalchemy import text  # noqa: E402


# class DashboardView(AdminIndexView):
#     """Custom admin index page with simple stats and quick links."""

#     @expose("/")
#     def index(self):
#         db = SessionLocal()
#         try:
#             job_count = db.query(Job).count()
#         except Exception:
#             job_count = 0
#         finally:
#             db.close()

#         # Build menu entries for Database Tables category so we can show
#         # a tree-style list of tables on the dashboard.
#         table_menus = []
#         if self.admin:
#             for item in self.admin.menu():
#                 if getattr(item, "category", None) == "Database Tables" and item.is_accessible():
#                     table_menus.append(item)

#         return self.render(
#             "admin/index.html",
#             job_count=job_count,
#             table_menus=table_menus,
#         )


# class ApiDocsView(BaseView):
#     """Menu item that links to Swagger API docs."""

#     @expose("/")
#     def index(self):
#         return redirect(url_for("flasgger.apidocs"))


# def get_public_tables():
#     """Return a list of table names in the public schema."""
#     with engine.connect() as conn:
#         result = conn.execute(
#             text(
#                 """
#                 SELECT table_name
#                 FROM information_schema.tables
#                 WHERE table_schema = 'public'
#                   AND table_type = 'BASE TABLE'
#                 ORDER BY table_name;
#                 """
#             )
#         )
#         return [row._mapping["table_name"] for row in result.fetchall()]


# def create_app() -> Flask:
#     """Application factory for the PHLC ingestion Flask app."""
#     app = Flask(__name__)

#     # Basic config
#     app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")

#     # Swagger / OpenAPI docs via flasgger
#     app.config["SWAGGER"] = {
#         "title": "PHLC Ingestion API",
#         "uiversion": 3,
#     }
#     Swagger(app)

#     # Initialize database (creates jobs table if missing)
#     init_db()

#     # SQLAlchemy session for Flask-Admin
#     db_session = scoped_session(SessionLocal)

#     @app.teardown_appcontext
#     def shutdown_session(exception=None):  # pragma: no cover - simple teardown
#         db_session.remove()

#     # Register API blueprint (all endpoints under /api/*)
#     app.register_blueprint(api_bp, url_prefix="/api")

#     # Flask-Admin setup with custom dashboard
#     admin = Admin(app, name="PHLC Admin", index_view=DashboardView())

#     # Register the explicit Job model first
#     admin.add_view(ModelView(Job, db_session, category="System"))

#     # "API Documentation" entry in the System section
#     admin.add_view(ApiDocsView(name="API Documentation", category="System"))

#     # Dynamically reflect and register all other public tables
#     metadata = MetaData()
#     inspector = inspect(engine)
#     for table_name in inspector.get_table_names(schema="public"):
#         if table_name == Job.__tablename__:
#             continue

#         table = Table(table_name, metadata, schema="public", autoload_with=engine)

#         # Create a dynamic SQLAlchemy model class backed by this table
#         model_name = f"Tbl_{table_name.capitalize()}"
#         DynamicModel = type(  # noqa: N806
#             model_name,
#             (Base,),
#             {"__table__": table, "__tablename__": table_name},
#         )

#         admin.add_view(
#             ModelView(DynamicModel, db_session, category="Database Tables")
#         )

#     # Redirect root to the admin dashboard (similar to ActiveAdmin)
#     @app.route("/")
#     def index():
#         return redirect(url_for("admin.index"))

#     # ---------------------------
#     # Database table dashboard UI
#     # ---------------------------

#     @app.route("/admin/tables/")
#     def table_dashboard_default():
#         """Redirect to the first available table dashboard."""
#         tables = get_public_tables()
#         if not tables:
#             abort(404, description="No tables found in database")
#         first = tables[0]
#         return redirect(url_for("table_dashboard", table_name=first))

#     @app.route("/admin/tables/<table_name>")
#     def table_dashboard(table_name: str):
#         """
#         Database table dashboard with left sidebar tree view and
#         paginated table data, similar to your reference UI.
#         """
#         tables = get_public_tables()
#         if not tables:
#             abort(404, description="No tables found in database")

#         if table_name not in tables:
#             abort(404, description="Table not found")

#         # Pagination
#         try:
#             page = int(request.args.get("page", 1))
#             per_page = int(request.args.get("per_page", 50))
#         except ValueError:
#             abort(400, description="Invalid pagination values")

#         page = max(page, 1)
#         per_page = max(1, min(per_page, 500))
#         offset = (page - 1) * per_page

#         with engine.connect() as conn:
#             # Get columns
#             cols_res = conn.execute(
#                 text(
#                     """
#                     SELECT column_name
#                     FROM information_schema.columns
#                     WHERE table_schema = 'public'
#                       AND table_name = :table_name
#                     ORDER BY ordinal_position;
#                     """
#                 ),
#                 {"table_name": table_name},
#             )
#             columns = [r._mapping["column_name"] for r in cols_res.fetchall()]

#             if not columns:
#                 abort(404, description="Table has no columns")

#             # Total rows
#             count_res = conn.execute(
#                 text(f'SELECT COUNT(*) AS total FROM "{table_name}";')
#             )
#             total_rows = int(count_res.scalar() or 0)

#             # Data
#             data_res = conn.execute(
#                 text(
#                     f'SELECT * FROM "{table_name}" ORDER BY 1 LIMIT :limit OFFSET :offset;'
#                 ),
#                 {"limit": per_page, "offset": offset},
#             )
#             rows = [dict(r._mapping) for r in data_res.fetchall()]

#         total_pages = max(1, math.ceil(total_rows / per_page)) if per_page else 1

#         return render_template(
#             "tables/dashboard.html",
#             tables=tables,
#             current_table=table_name,
#             columns=columns,
#             rows=rows,
#             page=page,
#             per_page=per_page,
#             total_pages=total_pages,
#             total_rows=total_rows,
#         )

#     return app


# app = create_app()


# if __name__ == "__main__":
#     # For direct python execution: python application.py
#     app.run(host="0.0.0.0", port=5000, debug=True)


# """
# Entry point for the Flask app - Using Flask-Admin's DEFAULT interface
# """

# import os
# import sys
# import math

# from flask import Flask, redirect, url_for, render_template, request, abort
# from flasgger import Swagger
# from flask_admin import Admin, BaseView, expose
# from flask_admin.contrib.sqla import ModelView
# from sqlalchemy import inspect, Table, MetaData
# from sqlalchemy.orm import scoped_session

# # --- Ensure project root is first on sys.path ---
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# if BASE_DIR not in sys.path:
#     sys.path.insert(0, BASE_DIR)
# # ------------------------------------------------

# from app.db import init_db, engine, SessionLocal  # noqa: E402
# from app.models import Base, Job  # noqa: E402
# from app.api import api_bp  # noqa: E402
# from sqlalchemy import text  # noqa: E402


# class ApiDocsView(BaseView):
#     """Menu item that links to Swagger API docs."""

#     @expose("/")
#     def index(self):
#         return redirect(url_for("flasgger.apidocs"))


# class CustomModelView(ModelView):
#     """
#     Custom ModelView with all CRUD operations enabled.
#     This enables edit, delete, create, view, and export functionality.
#     """
#     can_create = True
#     can_edit = True
#     can_delete = True
#     can_view_details = True
#     can_export = True
#     page_size = 50


# def get_public_tables():
#     """Return a list of table names in the public schema."""
#     with engine.connect() as conn:
#         result = conn.execute(
#             text(
#                 """
#                 SELECT table_name
#                 FROM information_schema.tables
#                 WHERE table_schema = 'public'
#                   AND table_type = 'BASE TABLE'
#                 ORDER BY table_name;
#                 """
#             )
#         )
#         return [row._mapping["table_name"] for row in result.fetchall()]


# def create_app() -> Flask:
#     """Application factory for the PHLC ingestion Flask app."""
#     app = Flask(__name__)

#     # Basic config
#     app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")

#     # Swagger / OpenAPI docs via flasgger
#     app.config["SWAGGER"] = {
#         "title": "PHLC Ingestion API",
#         "uiversion": 3,
#     }
#     Swagger(app)

#     # Initialize database (creates jobs table if missing)
#     init_db()

#     # SQLAlchemy session for Flask-Admin
#     db_session = scoped_session(SessionLocal)

#     @app.teardown_appcontext
#     def shutdown_session(exception=None):  # pragma: no cover - simple teardown
#         db_session.remove()

#     # Register API blueprint (all endpoints under /api/*)
#     app.register_blueprint(api_bp, url_prefix="/api")

#     # Flask-Admin setup - Using DEFAULT Flask-Admin interface (no custom dashboard)
#     admin = Admin(app, name="PHLC Admin", template_mode='bootstrap3')

#     # Register the explicit Job model first with CustomModelView
#     print("=" * 80)
#     print("REGISTERING MODELS WITH FLASK-ADMIN")
#     print("=" * 80)
    
#     try:
#         admin.add_view(CustomModelView(Job, db_session, category="System"))
#         print("✅ Successfully registered Job model")
#     except Exception as e:
#         print(f"❌ Failed to register Job model: {e}")

#     # "API Documentation" entry in the System section
#     admin.add_view(ApiDocsView(name="API Documentation", category="System"))

#     # Dynamically reflect and register all other public tables
#     print("\n" + "=" * 80)
#     print("DISCOVERING DATABASE TABLES")
#     print("=" * 80)
    
#     metadata = MetaData()
#     inspector = inspect(engine)
    
#     all_tables = inspector.get_table_names(schema="public")
#     print(f"Found {len(all_tables)} tables in database: {all_tables}")
    
#     tables_registered = 0
#     tables_failed = 0
    
#     for table_name in all_tables:
#         if table_name == Job.__tablename__:
#             print(f"⏭️  Skipping '{table_name}' (already registered as Job model)")
#             continue

#         print(f"\n📋 Processing table: '{table_name}'")
        
#         try:
#             # Reflect the table from the database
#             print(f"   Step 1: Reflecting table structure...")
#             table = Table(table_name, metadata, schema="public", autoload_with=engine)
#             print(f"   ✅ Table reflected with {len(table.columns)} columns")
            
#             # Get primary key constraint information
#             print(f"   Step 2: Detecting primary keys...")
#             pk_constraint = inspector.get_pk_constraint(table_name)
#             pk_columns = pk_constraint.get('constrained_columns', [])
            
#             if pk_columns:
#                 print(f"   ✅ Found primary key(s): {pk_columns}")
#             else:
#                 print(f"   ⚠️  No primary key defined, using fallback...")
                
#             # Fallback strategy if no primary key is defined
#             if not pk_columns:
#                 if 'id' in [col.name for col in table.columns]:
#                     pk_columns = ['id']
#                     print(f"   ✅ Using 'id' column as primary key")
#                 elif len(table.columns) > 0:
#                     pk_columns = [list(table.columns)[0].name]
#                     print(f"   ✅ Using first column '{pk_columns[0]}' as primary key")
            
#             # Create dynamic model class with explicit primary key mapping
#             print(f"   Step 3: Creating SQLAlchemy model...")
#             model_name = f"Tbl_{table_name.replace('_', '').capitalize()}"
#             DynamicModel = type(
#                 model_name,
#                 (Base,),
#                 {
#                     '__table__': table,
#                     '__tablename__': table_name,
#                     '__mapper_args__': {
#                         'primary_key': [table.c[col] for col in pk_columns] if pk_columns else [list(table.columns)[0]]
#                     }
#                 }
#             )
#             print(f"   ✅ Created model class: {model_name}")
            
#             # Register the model with Flask-Admin using CustomModelView
#             print(f"   Step 4: Registering with Flask-Admin...")
#             admin.add_view(
#                 CustomModelView(DynamicModel, db_session, category="Database Tables", name=table_name)
#             )
#             print(f"   ✅ Successfully registered '{table_name}' with Flask-Admin")
#             tables_registered += 1
            
#         except Exception as e:
#             print(f"   ❌ ERROR processing table '{table_name}': {e}")
#             import traceback
#             traceback.print_exc()
#             tables_failed += 1
#             continue

#     print("\n" + "=" * 80)
#     print(f"SUMMARY: {tables_registered} tables registered, {tables_failed} failed")
#     print("=" * 80 + "\n")

#     # Redirect root to the admin dashboard
#     @app.route("/")
#     def index():
#         return redirect(url_for("admin.index"))

#     # ---------------------------
#     # Database table dashboard UI (your custom dashboard)
#     # ---------------------------

#     @app.route("/admin/tables/")
#     def table_dashboard_default():
#         """Redirect to the first available table dashboard."""
#         tables = get_public_tables()
#         if not tables:
#             abort(404, description="No tables found in database")
#         first = tables[0]
#         return redirect(url_for("table_dashboard", table_name=first))

#     @app.route("/admin/tables/<table_name>")
#     def table_dashboard(table_name: str):
#         """
#         Database table dashboard with left sidebar tree view and
#         paginated table data, similar to your reference UI.
#         """
#         tables = get_public_tables()
#         if not tables:
#             abort(404, description="No tables found in database")

#         if table_name not in tables:
#             abort(404, description="Table not found")

#         # Pagination
#         try:
#             page = int(request.args.get("page", 1))
#             per_page = int(request.args.get("per_page", 50))
#         except ValueError:
#             abort(400, description="Invalid pagination values")

#         page = max(page, 1)
#         per_page = max(1, min(per_page, 500))
#         offset = (page - 1) * per_page

#         with engine.connect() as conn:
#             # Get columns
#             cols_res = conn.execute(
#                 text(
#                     """
#                     SELECT column_name
#                     FROM information_schema.columns
#                     WHERE table_schema = 'public'
#                       AND table_name = :table_name
#                     ORDER BY ordinal_position;
#                     """
#                 ),
#                 {"table_name": table_name},
#             )
#             columns = [r._mapping["column_name"] for r in cols_res.fetchall()]

#             if not columns:
#                 abort(404, description="Table has no columns")

#             # Total rows
#             count_res = conn.execute(
#                 text(f'SELECT COUNT(*) AS total FROM "{table_name}";')
#             )
#             total_rows = int(count_res.scalar() or 0)

#             # Data
#             data_res = conn.execute(
#                 text(
#                     f'SELECT * FROM "{table_name}" ORDER BY 1 LIMIT :limit OFFSET :offset;'
#                 ),
#                 {"limit": per_page, "offset": offset},
#             )
#             rows = [dict(r._mapping) for r in data_res.fetchall()]

#         total_pages = max(1, math.ceil(total_rows / per_page)) if per_page else 1

#         return render_template(
#             "admin/dashboard.html",
#             tables=tables,
#             current_table=table_name,
#             columns=columns,
#             rows=rows,
#             page=page,
#             per_page=per_page,
#             total_pages=total_pages,
#             total_rows=total_rows,
#         )

#     return app


# app = create_app()


# if __name__ == "__main__":
#     # For direct python execution: python application.py
#     app.run(host="0.0.0.0", port=5000, debug=True)


# """
# Entry point for the Flask app - Custom Navigation with Flask-Admin CRUD
# """

# import os
# import sys
# import math

# from flask import Flask, redirect, url_for, render_template, request, abort
# from flasgger import Swagger
# from flask_admin import Admin, AdminIndexView, BaseView, expose
# from flask_admin.contrib.sqla import ModelView
# from sqlalchemy import inspect, Table, MetaData
# from sqlalchemy.orm import scoped_session

# # --- Ensure project root is first on sys.path ---
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# if BASE_DIR not in sys.path:
#     sys.path.insert(0, BASE_DIR)
# # ------------------------------------------------

# from app.db import init_db, engine, SessionLocal  # noqa: E402
# from app.models import Base, Job  # noqa: E402
# from app.api import api_bp  # noqa: E402
# from sqlalchemy import text  # noqa: E402


# class CustomAdminIndexView(AdminIndexView):
#     """Custom home page with navigation sidebar"""
    
#     @expose('/')
#     def index(self):
#         # Get all registered table views
#         table_views = []
#         for view in self.admin._views:
#             if hasattr(view, 'category') and view.category == "Database Tables":
#                 table_views.append({
#                     'name': view.name,
#                     'url': view.url
#                 })
        
#         return self.render('admin/custom_home.html', table_views=table_views)


# class DashboardView(BaseView):
#     """Dashboard showing all tables in tree structure"""
    
#     @expose('/')
#     def index(self):
#         # Get all registered table views
#         table_views = []
#         for view in self.admin._views:
#             if hasattr(view, 'category') and view.category == "Database Tables":
#                 table_views.append({
#                     'name': view.name,
#                     'url': view.url
#                 })
        
#         return self.render('admin/custom_dashboard.html', table_views=table_views)


# class ApiDocsView(BaseView):
#     """API Documentation page with sidebar."""

#     @expose("/")
#     def index(self):
#         # Get all registered table views
#         table_views = []
#         for view in self.admin._views:
#             if hasattr(view, 'category') and view.category == "Database Tables":
#                 table_views.append({
#                     'name': view.name,
#                     'url': view.url
#                 })
        
#         return self.render('admin/custom_api.html', table_views=table_views)


# class CustomModelView(ModelView):
#     """
#     Custom ModelView with all CRUD operations enabled.
#     Uses custom base template with sidebar.
#     """
#     can_create = True
#     can_edit = True
#     can_delete = True
#     can_view_details = True
#     can_export = True
#     page_size = 50


# def get_public_tables():
#     """Return a list of table names in the public schema."""
#     with engine.connect() as conn:
#         result = conn.execute(
#             text(
#                 """
#                 SELECT table_name
#                 FROM information_schema.tables
#                 WHERE table_schema = 'public'
#                   AND table_type = 'BASE TABLE'
#                 ORDER BY table_name;
#                 """
#             )
#         )
#         return [row._mapping["table_name"] for row in result.fetchall()]


# def create_app() -> Flask:
#     """Application factory for the PHLC ingestion Flask app."""
#     app = Flask(__name__)

#     # Basic config
#     app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")

#     # Swagger / OpenAPI docs via flasgger
#     app.config["SWAGGER"] = {
#         "title": "PHLC Ingestion API",
#         "uiversion": 3,
#     }
#     Swagger(app)

#     # Initialize database (creates jobs table if missing)
#     init_db()

#     # SQLAlchemy session for Flask-Admin
#     db_session = scoped_session(SessionLocal)

#     @app.teardown_appcontext
#     def shutdown_session(exception=None):  # pragma: no cover - simple teardown
#         db_session.remove()

#     # Register API blueprint (all endpoints under /api/*)
#     app.register_blueprint(api_bp, url_prefix="/api")

#     # Flask-Admin setup with custom home page
#     admin = Admin(
#         app, 
#         name="PHLC Admin", 
#         template_mode='bootstrap3',
#         index_view=CustomAdminIndexView(name='Home', url='/admin')
#     )

#     # Add Dashboard view
#     admin.add_view(DashboardView(name='Dashboard', endpoint='dashboard'))

#     # Add API Documentation view
#     admin.add_view(ApiDocsView(name="API", endpoint='api_docs'))

#     # Register the explicit Job model
#     print("=" * 80)
#     print("REGISTERING MODELS WITH FLASK-ADMIN")
#     print("=" * 80)
    
#     try:
#         admin.add_view(CustomModelView(Job, db_session, category="Database Tables"))
#         print("✅ Successfully registered Job model")
#     except Exception as e:
#         print(f"❌ Failed to register Job model: {e}")

#     # Dynamically reflect and register all other public tables
#     print("\n" + "=" * 80)
#     print("DISCOVERING DATABASE TABLES")
#     print("=" * 80)
    
#     metadata = MetaData()
#     inspector = inspect(engine)
    
#     all_tables = inspector.get_table_names(schema="public")
#     print(f"Found {len(all_tables)} tables in database")
    
#     tables_registered = 0
#     tables_failed = 0
    
#     for table_name in all_tables:
#         if table_name == Job.__tablename__:
#             print(f"⏭️  Skipping '{table_name}' (already registered)")
#             continue

#         print(f"📋 Processing table: '{table_name}'")
        
#         try:
#             # Reflect the table
#             table = Table(table_name, metadata, schema="public", autoload_with=engine)
            
#             # Get primary key columns
#             pk_constraint = inspector.get_pk_constraint(table_name)
#             pk_columns = pk_constraint.get('constrained_columns', [])
            
#             # Fallback strategy if no primary key
#             if not pk_columns:
#                 if 'id' in [col.name for col in table.columns]:
#                     pk_columns = ['id']
#                 elif len(table.columns) > 0:
#                     pk_columns = [list(table.columns)[0].name]
            
#             # Create dynamic model class
#             model_name = f"Tbl_{table_name.replace('_', '').capitalize()}"
#             DynamicModel = type(
#                 model_name,
#                 (Base,),
#                 {
#                     '__table__': table,
#                     '__tablename__': table_name,
#                     '__mapper_args__': {
#                         'primary_key': [table.c[col] for col in pk_columns] if pk_columns else [list(table.columns)[0]]
#                     }
#                 }
#             )
            
#             # Register with Flask-Admin
#             admin.add_view(
#                 CustomModelView(DynamicModel, db_session, category="Database Tables", name=table_name)
#             )
#             print(f"   ✅ Registered '{table_name}'")
#             tables_registered += 1
            
#         except Exception as e:
#             print(f"   ❌ ERROR: {e}")
#             tables_failed += 1
#             continue

#     print(f"\n✅ SUMMARY: {tables_registered} tables registered, {tables_failed} failed\n")

#     # Redirect root to admin home
#     @app.route("/")
#     def index():
#         return redirect(url_for("admin.index"))

#     return app


# app = create_app()


# if __name__ == "__main__":
#     app.run(host="0.0.0.0", port=5000, debug=True)


"""
Entry point for the Flask app - Custom Navigation with Flask-Admin CRUD
"""

import os
import sys
import math

from flask import Flask, redirect, url_for, render_template, request, abort
from flasgger import Swagger
from flask_admin import Admin, AdminIndexView, BaseView, expose
from flask_admin.contrib.sqla import ModelView
from sqlalchemy import inspect, Table, MetaData
from sqlalchemy.orm import scoped_session

# --- Ensure project root is first on sys.path ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
# ------------------------------------------------

from app.db import init_db, engine, SessionLocal  # noqa: E402
from app.models import Base, Job  # noqa: E402
from app.api import api_bp  # noqa: E402
from sqlalchemy import text  # noqa: E402


class CustomAdminIndexView(AdminIndexView):
    """Custom home page with navigation sidebar"""
    
    @expose('/')
    def index(self):
        return self.render('admin/custom_home.html')


class DashboardView(BaseView):
    """Dashboard showing all tables in tree structure"""
    
    @expose('/')
    def index(self):
        # Get all registered table views
        table_views = []
        for view in self.admin._views:
            if hasattr(view, 'category') and view.category == "Database Tables":
                table_views.append({
                    'name': view.name,
                    'url': view.url
                })
        
        return self.render('admin/custom_dashboard.html', table_views=table_views)


class ApiDocsView(BaseView):
    """API Documentation page with sidebar."""

    @expose("/")
    def index(self):
        return self.render('admin/custom_api.html')


class CustomModelView(ModelView):
    """
    Custom ModelView with all CRUD operations enabled.
    Uses custom base template with sidebar.
    """
    can_create = True
    can_edit = True
    can_delete = True
    can_view_details = True
    can_export = True
    page_size = 50


def get_public_tables():
    """Return a list of table names in the public schema."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name;
                """
            )
        )
        return [row._mapping["table_name"] for row in result.fetchall()]


def create_app() -> Flask:
    """Application factory for the PHLC ingestion Flask app."""
    app = Flask(__name__)

    # Basic config
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-key")

    # Swagger / OpenAPI docs via flasgger
    app.config["SWAGGER"] = {
        "title": "PHLC Ingestion API",
        "uiversion": 3,
    }
    Swagger(app)

    # Initialize database (creates jobs table if missing)
    init_db()

    # SQLAlchemy session for Flask-Admin
    db_session = scoped_session(SessionLocal)

    @app.teardown_appcontext
    def shutdown_session(exception=None):  # pragma: no cover - simple teardown
        db_session.remove()

    # Register API blueprint (all endpoints under /api/*)
    app.register_blueprint(api_bp, url_prefix="/api")

    # Flask-Admin setup with custom home page
    admin = Admin(
        app, 
        name="PHLC Admin", 
        template_mode='bootstrap3',
        index_view=CustomAdminIndexView(name='Home', url='/admin')
    )

    # Add Dashboard view
    admin.add_view(DashboardView(name='Dashboard', endpoint='dashboard'))

    # Add API Documentation view
    admin.add_view(ApiDocsView(name="API", endpoint='api_docs'))

    # Register the explicit Job model
    print("=" * 80)
    print("REGISTERING MODELS WITH FLASK-ADMIN")
    print("=" * 80)
    
    try:
        admin.add_view(CustomModelView(Job, db_session, category="Database Tables"))
        print("✅ Successfully registered Job model")
    except Exception as e:
        print(f"❌ Failed to register Job model: {e}")

    # Dynamically reflect and register all other public tables
    print("\n" + "=" * 80)
    print("DISCOVERING DATABASE TABLES")
    print("=" * 80)
    
    metadata = MetaData()
    inspector = inspect(engine)
    
    all_tables = inspector.get_table_names(schema="public")
    print(f"Found {len(all_tables)} tables in database")
    
    tables_registered = 0
    tables_failed = 0
    
    for table_name in all_tables:
        if table_name == Job.__tablename__:
            print(f"⏭️  Skipping '{table_name}' (already registered)")
            continue

        print(f"📋 Processing table: '{table_name}'")
        
        try:
            # Reflect the table
            table = Table(table_name, metadata, schema="public", autoload_with=engine)
            
            # Get primary key columns
            pk_constraint = inspector.get_pk_constraint(table_name)
            pk_columns = pk_constraint.get('constrained_columns', [])
            
            # Fallback strategy if no primary key
            if not pk_columns:
                if 'id' in [col.name for col in table.columns]:
                    pk_columns = ['id']
                elif len(table.columns) > 0:
                    pk_columns = [list(table.columns)[0].name]
            
            # Create dynamic model class
            model_name = f"Tbl_{table_name.replace('_', '').capitalize()}"
            DynamicModel = type(
                model_name,
                (Base,),
                {
                    '__table__': table,
                    '__tablename__': table_name,
                    '__mapper_args__': {
                        'primary_key': [table.c[col] for col in pk_columns] if pk_columns else [list(table.columns)[0]]
                    }
                }
            )
            
            # Register with Flask-Admin
            admin.add_view(
                CustomModelView(DynamicModel, db_session, category="Database Tables", name=table_name)
            )
            print(f"   ✅ Registered '{table_name}'")
            tables_registered += 1
            
        except Exception as e:
            print(f"   ❌ ERROR: {e}")
            tables_failed += 1
            continue

    print(f"\n✅ SUMMARY: {tables_registered} tables registered, {tables_failed} failed\n")

    # Redirect root to admin home
    @app.route("/")
    def index():
        return redirect(url_for("admin.index"))

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)

