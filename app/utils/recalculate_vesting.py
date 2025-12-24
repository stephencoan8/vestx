"""
Script to recalculate vesting schedules for all grants.
Use this after updating the vest_calculator logic to update existing grants.
"""

from app import create_app, db
from app.models.grant import Grant
from app.models.vest_event import VestEvent
from app.utils.vest_calculator import calculate_vest_schedule


def recalculate_all_vesting_schedules():
    """Recalculate vesting schedules for all grants in the database."""
    app = create_app()
    
    with app.app_context():
        # Get all grants
        grants = Grant.query.all()
        
        print(f"Found {len(grants)} grants to recalculate...")
        
        for grant in grants:
            print(f"\nRecalculating grant #{grant.id} ({grant.grant_type}, {grant.share_type})...")
            
            # Delete existing vest events
            VestEvent.query.filter_by(grant_id=grant.id).delete()
            
            # Calculate new vest schedule
            vest_schedule = calculate_vest_schedule(grant)
            
            # Create new vest events
            for vest_info in vest_schedule:
                vest_event = VestEvent(
                    grant_id=grant.id,
                    vest_date=vest_info['vest_date'],
                    shares_vested=vest_info['shares']
                )
                db.session.add(vest_event)
            
            print(f"  Created {len(vest_schedule)} vest events")
        
        # Commit all changes
        db.session.commit()
        print(f"\n✅ Successfully recalculated vesting schedules for {len(grants)} grants!")


if __name__ == '__main__':
    recalculate_all_vesting_schedules()
