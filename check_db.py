from app import create_app
from app.models.vest_event import VestEvent
from app.models.grant import Grant

app = create_app()
ctx = app.app_context()
ctx.push()

vests = VestEvent.query.all()
grants = Grant.query.all()

print(f'Total grants: {len(grants)}')
print(f'Total vests: {len(vests)}')

if grants:
    g = grants[0]
    print(f'First grant: {g.id}, type: {g.grant_type}, user: {g.user_id}')
    vests_for_grant = VestEvent.query.filter_by(grant_id=g.id).all()
    print(f'Vests for grant {g.id}: {len(vests_for_grant)}')

ctx.pop()
