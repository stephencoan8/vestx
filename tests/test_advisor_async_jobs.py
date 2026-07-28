"""Async advisor job queue — enqueue returns immediately; worker fills result."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_slim_engine_plan():
    from app.utils.advisor_service import slim_engine_plan
    out = slim_engine_plan({'picks': [1], 'tax_analysis': {'heavy': True}, 'alternatives': [{'tax_analysis': 1, 'x': 2}]})
    assert out['picks'] == [1]
    assert 'tax_analysis' not in out
    assert 'tax_analysis' not in out['alternatives'][0]
    assert out['alternatives'][0]['x'] == 2


def test_enqueue_and_complete_engine_only():
    from app import create_app, db
    from app.models.user import User
    from app.utils.advisor_jobs import enqueue_advisor_job, get_job_for_user

    app = create_app()
    with app.app_context():
        u = User.query.first()
        if not u:
            # Create minimal user for isolated CI if needed
            return
        job = enqueue_advisor_job(
            user_id=u.id,
            messages=[{
                'role': 'user',
                'content': 'what should I sell to get 50k and minimize my taxes?',
            }],
        )
        assert job.id
        assert job.status == 'queued'
        jid = job.id
        done = None
        for _ in range(60):
            time.sleep(0.2)
            db.session.expire_all()
            j = get_job_for_user(jid, u.id)
            assert j is not None
            if j.status in ('done', 'error'):
                done = j
                break
        assert done is not None, 'job did not finish'
        assert done.status == 'done', done.error
        result = done.get_result()
        assert result.get('success') is True
        assert result.get('reply')
        assert result.get('phase') in ('engine_done', 'engine_done_no_key', 'grok_done')


if __name__ == '__main__':
    test_slim_engine_plan()
    test_enqueue_and_complete_engine_only()
    print('ADVISOR ASYNC JOB TESTS PASSED')
